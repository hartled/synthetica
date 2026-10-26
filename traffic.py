"""Daily page-view and checkout counters, plus sanitized UTM tokens.

Counts live in traffic_daily in the same SQLite file as the rest of the site
(DATABASE_PATH, the persistent disk at /data on Render). A short in-memory
batch is flushed with one UPSERT, on a timer checked from counted requests,
when an admin reads the totals, and on process shutdown. A failed write is
kept in memory and never fails the request.

Nothing here stores an IP address, a cookie, a user agent, or an account id.
Report ids are not stored: a paid report page is the path group "page".
Role slugs are catalog ids, not people.

/admin/funnel still reads funnel_events and groups by src (card, lab, home).
It does not segment by UTM. This module does.
"""

import csv
import io
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response

UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
_TOKEN = re.compile(r"\A[A-Za-z0-9._~-]{1,64}\Z")
_SLUG = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_IDENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_OWN_UA = re.compile(r"MindGuildWaitlist|MindGuildReports|MindGuildSim|synthetica-sim", re.I)
_COOKIE = "mg_utm"
_COOKIE_MAX_AGE = 7 * 24 * 60 * 60
_FLUSH_BATCH = 20
_FLUSH_SECONDS = 30
_BUSY_MS = 250
_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_CSV_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")

_lock = threading.Lock()
_pending = {}
_dirty_at = 0.0
_installed = False


def clean_token(value):
    """A campaign token, or empty when the value is missing or not allowed."""
    if value is None:
        return ""
    text = str(value).strip()
    if _TOKEN.match(text):
        return text
    return ""


def column_values(parts):
    """Four nullable tokens for a table column. Empty becomes None."""
    if isinstance(parts, dict):
        parts = tuple(parts.get(name) for name in UTM_FIELDS)
    elif not parts:
        return (None, None, None, None)
    out = []
    for part in list(parts)[:4]:
        token = clean_token(part)
        out.append(token or None)
    while len(out) < 4:
        out.append(None)
    return tuple(out[:4])


def _parts_from_values(values):
    parts = tuple(clean_token(value) for value in list(values)[:4])
    if len(parts) < 4:
        parts = parts + ("",) * (4 - len(parts))
    if not any(parts):
        return None
    return parts


def utm_from_mapping(data):
    if not isinstance(data, dict):
        return None
    return _parts_from_values(data.get(name) for name in UTM_FIELDS)


def _cookie_pair(header):
    if not header:
        return ""
    for part in str(header).split(";"):
        part = part.strip()
        prefix = _COOKIE + "="
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


def utm_from_cookie_header(header):
    raw = _cookie_pair(header)
    if not raw or len(raw) > 300 or raw.count("|") != 3:
        return None
    return _parts_from_values(raw.split("|"))


def utm_from_query(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1", "replace")
    from urllib.parse import parse_qs
    parsed = parse_qs(raw or "", keep_blank_values=False)
    return utm_from_mapping({name: (parsed.get(name) or [""])[-1] for name in UTM_FIELDS})


def parts_from(request, data=None):
    """UTM for a signup or checkout. Form fields win, then the first-touch cookie, then the query."""
    submitted = utm_from_mapping(data)
    if submitted:
        return submitted
    if request is None:
        return ("", "", "", "")
    query = utm_from_mapping({name: request.query_params.get(name) for name in UTM_FIELDS})
    cookie = utm_from_cookie_header(request.headers.get("cookie"))
    if cookie:
        return cookie
    if query:
        return query
    return ("", "", "", "")


def hidden_inputs():
    return "".join(
        '<input type="hidden" name="{0}" value="">'.format(name) for name in UTM_FIELDS
    )


def ensure_utm_columns(c, table):
    """Add the four UTM columns when missing. Does not rewrite existing rows."""
    if not _IDENT.match(table or ""):
        return
    have = {row[1] for row in c.execute("PRAGMA table_info(%s)" % table)}
    for name in UTM_FIELDS:
        if name not in have:
            c.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (table, name))


def ensure(c):
    """Create traffic_daily if missing. Safe to call more than once.

    Posts and the search index are left alone.
    """
    c.execute(
        """CREATE TABLE IF NOT EXISTS traffic_daily(
            day TEXT NOT NULL,
            path_group TEXT NOT NULL,
            path_key TEXT NOT NULL,
            utm_source TEXT NOT NULL DEFAULT '',
            utm_medium TEXT NOT NULL DEFAULT '',
            utm_campaign TEXT NOT NULL DEFAULT '',
            utm_content TEXT NOT NULL DEFAULT '',
            hits INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (
                day, path_group, path_key,
                utm_source, utm_medium, utm_campaign, utm_content
            ))"""
    )


def _header(scope, name):
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1", "replace")
    return ""


def _skip_ua(value):
    import funnel
    if funnel.is_bot_user_agent(value):
        return True
    return _OWN_UA.search(value or "") is not None


def _plain_path(path):
    text = path or "/"
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def _passthrough(path):
    if path == "/healthz" or path.startswith("/healthz/"):
        return True
    if path == "/health" or path.startswith("/health/"):
        return True
    if path in ("/readyz", "/livez"):
        return True
    if path == "/admin" or path.startswith("/admin/"):
        return True
    return False


def classify(method, path, status, content_type):
    """A (path_group, path_key) for a real page view, or None."""
    if method != "GET" or status != 200:
        return None
    if "text/html" not in (content_type or "").lower():
        return None
    path = _plain_path(path)
    if path == "/reports":
        return ("reports", "index")
    if path == "/reports/sample":
        return ("reports", "sample")
    if path == "/reports/success":
        return ("reports", "success")
    if path.startswith("/reports/") and path.count("/") == 2 and not path.endswith(".pdf"):
        return ("reports", "page")
    if path == "/roles":
        return ("roles", "index")
    if path.startswith("/roles/") and path.count("/") == 2:
        slug = path.split("/", 2)[2]
        if _SLUG.match(slug) and len(slug) <= 64:
            return ("roles", slug)
    return None


def classify_checkout(method, path, status):
    if method == "POST" and status == 200 and _plain_path(path) == "/api/reports/checkout":
        return ("checkout", "start")
    return None


def note_human(request, group, key, parts):
    """Count one human action. Bots and the app's own clients are skipped."""
    try:
        ua = ""
        if request is not None:
            ua = request.headers.get("user-agent") or ""
        if _skip_ua(ua):
            return
        note(group, key, parts)
    except Exception:
        print("traffic note failed", flush=True)


def _utc_day(now=None):
    return datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).strftime("%Y-%m-%d")


def note(group, key, parts, now=None):
    """Add one hit to the in-memory batch. Never raises."""
    try:
        day = _utc_day(now)
        utm = parts or ("", "", "", "")
        utm = tuple(clean_token(part) for part in list(utm)[:4])
        if len(utm) < 4:
            utm = utm + ("",) * (4 - len(utm))
        bucket = (day, group, key) + utm
        due = False
        with _lock:
            global _dirty_at
            _pending[bucket] = _pending.get(bucket, 0) + 1
            if not _dirty_at:
                _dirty_at = time.monotonic()
            pending = sum(_pending.values())
            age = time.monotonic() - _dirty_at
            due = pending >= _FLUSH_BATCH or age >= _FLUSH_SECONDS
        if due:
            flush()
    except Exception:
        print("traffic note failed", flush=True)


def _write_batch(batch):
    import app as appmod

    c = appmod.db()
    try:
        c.execute("PRAGMA busy_timeout=%d" % _BUSY_MS)
        ensure(c)
        c.executemany(
            """INSERT INTO traffic_daily(
                   day, path_group, path_key,
                   utm_source, utm_medium, utm_campaign, utm_content, hits)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(day, path_group, path_key, utm_source, utm_medium, utm_campaign, utm_content)
               DO UPDATE SET hits = hits + excluded.hits""",
            [(*key, count) for key, count in batch.items()],
        )
        c.commit()
    finally:
        c.close()


def flush():
    """UPSERT the batch. On failure the counts stay in memory. Never raises."""
    global _dirty_at
    with _lock:
        if not _pending:
            _dirty_at = 0.0
            return
        batch = dict(_pending)
        _pending.clear()
        _dirty_at = 0.0
    try:
        _write_batch(batch)
    except Exception:
        print("traffic flush failed", flush=True)
        with _lock:
            for key, count in batch.items():
                _pending[key] = _pending.get(key, 0) + count
            if not _dirty_at:
                _dirty_at = time.monotonic()


def reset_memory():
    """Drop the unflushed batch. Tests use this between cases."""
    global _dirty_at
    with _lock:
        _pending.clear()
        _dirty_at = 0.0


def _secure_cookie():
    try:
        import app as appmod
        return bool(appmod.COOKIE_SECURE)
    except Exception:
        return False


def _cookie_bytes(parts):
    value = "|".join(parts)
    bits = [
        "%s=%s" % (_COOKIE, value),
        "Path=/",
        "Max-Age=%d" % _COOKIE_MAX_AGE,
        "SameSite=Lax",
        "HttpOnly",
    ]
    if _secure_cookie():
        bits.append("Secure")
    return "; ".join(bits).encode("ascii")


def _content_type(message):
    for key, value in message.get("headers") or ():
        if key.lower() == b"content-type":
            return value.decode("latin-1", "replace")
    return ""


class TrafficMiddleware:
    """Count real HTML page loads and checkout starts. Failures stay local."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method") or ""
        path = _plain_path(scope.get("path") or "/")
        if _passthrough(path):
            await self.app(scope, receive, send)
            return
        ua = _header(scope, b"user-agent")
        query_utm = utm_from_query(scope.get("query_string") or b"")
        cookie_utm = utm_from_cookie_header(_header(scope, b"cookie"))
        parts = cookie_utm or query_utm or ("", "", "", "")
        set_cookie = bool(query_utm) and not cookie_utm and method == "GET" and not _skip_ua(ua)
        seen = {"status": 0, "ctype": ""}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                seen["status"] = int(message.get("status") or 0)
                seen["ctype"] = _content_type(message)
                if set_cookie and seen["status"] == 200 and "text/html" in seen["ctype"].lower():
                    headers = list(message.get("headers") or [])
                    headers.append((b"set-cookie", _cookie_bytes(query_utm)))
                    message = dict(message)
                    message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if _skip_ua(ua):
                return
            try:
                hit = classify(method, path, seen["status"], seen["ctype"])
                if hit is not None:
                    note(hit[0], hit[1], parts)
            except Exception:
                print("traffic count failed", flush=True)


def _clamp_days(days):
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 14
    if value < 1:
        return 1
    if value > 366:
        return 366
    return value


def _window(days, now=None):
    days = _clamp_days(days)
    moment = now if now is not None else time.time()
    until = datetime.fromtimestamp(moment, timezone.utc).date()
    since = until - timedelta(days=days - 1)
    start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    end = datetime(until.year, until.month, until.day, tzinfo=timezone.utc) + timedelta(days=1)
    return days, since, until, start.timestamp(), end.timestamp()


def _role_title(slug):
    if slug in ("", "index"):
        return "All roles"
    import career_data
    for entry in career_data.REAL_CAREERS:
        if entry["id"] == slug:
            return entry["title"]
    return slug


def _empty_day(day):
    return {
        "day": day,
        "report_views": 0,
        "role_views": 0,
        "checkout_starts": 0,
        "reports": {"index": 0, "sample": 0, "page": 0, "success": 0},
        "roles": {},
    }


def _add_utm(bucket, parts, **counts):
    key = parts if len(parts) == 4 else ("", "", "", "")
    row = bucket.get(key)
    if row is None:
        row = {
            "utm_source": key[0],
            "utm_medium": key[1],
            "utm_campaign": key[2],
            "utm_content": key[3],
        }
        bucket[key] = row
    for name, count in counts.items():
        row[name] = int(row.get(name) or 0) + int(count)


def _utm_rows(bucket, count_keys):
    rows = []
    for row in bucket.values():
        item = {
            "utm_source": row["utm_source"],
            "utm_medium": row["utm_medium"],
            "utm_campaign": row["utm_campaign"],
            "utm_content": row["utm_content"],
        }
        for name in count_keys:
            item[name] = int(row.get(name) or 0)
        extra = {key: value for key, value in row.items() if key not in item}
        item.update(extra)
        rows.append(item)
    rows.sort(key=lambda item: (-sum(int(item.get(name) or 0) for name in count_keys), item["utm_source"], item["utm_campaign"]))
    return rows


def summary(c, days=14, now=None):
    """Aggregate counters for the admin API. Caller flushes first."""
    days, since, until, start_ts, end_ts = _window(days, now)
    ensure(c)
    for table in ("funnel_events", "role_snapshot_signups", "product_waitlist"):
        if _has_table(c, table):
            ensure_utm_columns(c, table)
    view_rows = c.execute(
        """SELECT day, path_group, path_key, utm_source, utm_medium, utm_campaign, utm_content, hits
           FROM traffic_daily
           WHERE day >= ? AND day <= ?""",
        (since.isoformat(), until.isoformat()),
    ).fetchall()
    by_day = {}
    cursor = since
    while cursor <= until:
        by_day[cursor.isoformat()] = _empty_day(cursor.isoformat())
        cursor += timedelta(days=1)
    view_utm = {}
    checkout_utm = {}
    for row in view_rows:
        day = row["day"]
        if day not in by_day:
            continue
        group = row["path_group"]
        key = row["path_key"]
        hits = int(row["hits"])
        parts = (row["utm_source"], row["utm_medium"], row["utm_campaign"], row["utm_content"])
        slot = by_day[day]
        if group == "reports":
            slot["report_views"] += hits
            if key in slot["reports"]:
                slot["reports"][key] += hits
            else:
                slot["reports"][key] = slot["reports"].get(key, 0) + hits
            _add_utm(view_utm, parts, report_views=hits, role_views=0, count=hits)
        elif group == "roles":
            slot["role_views"] += hits
            slot["roles"][key] = slot["roles"].get(key, 0) + hits
            _add_utm(view_utm, parts, report_views=0, role_views=hits, count=hits)
        elif group == "checkout":
            slot["checkout_starts"] += hits
            _add_utm(checkout_utm, parts, count=hits)
    role_totals = {}
    for slot in by_day.values():
        for slug, hits in slot["roles"].items():
            role_totals[slug] = role_totals.get(slug, 0) + hits
    roles = []
    for slug, hits in sorted(role_totals.items(), key=lambda item: (-item[1], item[0])):
        roles.append({"slug": slug, "title": _role_title(slug), "views": hits})
    days_out = []
    for day in sorted(by_day):
        slot = by_day[day]
        role_rows = []
        for slug, hits in sorted(slot["roles"].items(), key=lambda item: (-item[1], item[0])):
            role_rows.append({"slug": slug, "title": _role_title(slug), "views": hits})
        days_out.append({
            "day": day,
            "report_views": slot["report_views"],
            "role_views": slot["role_views"],
            "checkout_starts": slot["checkout_starts"],
            "reports": slot["reports"],
            "roles": {"index": slot["roles"].get("index", 0), "by_role": [item for item in role_rows if item["slug"] != "index"]},
        })
    snap_utm = {}
    snap_total = 0
    if _has_table(c, "role_snapshot_signups"):
        snap_rows = c.execute(
            """SELECT COALESCE(utm_source, ''), COALESCE(utm_medium, ''),
                      COALESCE(utm_campaign, ''), COALESCE(utm_content, ''),
                      COUNT(*)
               FROM role_snapshot_signups
               WHERE created >= ? AND created < ?
               GROUP BY 1, 2, 3, 4""",
            (start_ts, end_ts),
        ).fetchall()
        for source, medium, campaign, content, count in snap_rows:
            snap_total += int(count)
            _add_utm(snap_utm, (source, medium, campaign, content), count=int(count))
    wait_bucket = {}
    wait_total = 0
    if _has_table(c, "product_waitlist"):
        wait_rows = c.execute(
            """SELECT product, COALESCE(utm_source, ''), COALESCE(utm_medium, ''),
                      COALESCE(utm_campaign, ''), COALESCE(utm_content, ''),
                      COUNT(*)
               FROM product_waitlist
               WHERE created >= ? AND created < ?
               GROUP BY 1, 2, 3, 4, 5""",
            (start_ts, end_ts),
        ).fetchall()
        for product, source, medium, campaign, content, count in wait_rows:
            wait_total += int(count)
            key = (product or "", source, medium, campaign, content)
            found = wait_bucket.get(key)
            if found is None:
                found = {
                    "product": product or "",
                    "utm_source": source,
                    "utm_medium": medium,
                    "utm_campaign": campaign,
                    "utm_content": content,
                    "count": 0,
                }
                wait_bucket[key] = found
            found["count"] += int(count)
    wait_list = sorted(
        wait_bucket.values(),
        key=lambda item: (-item["count"], item["product"], item["utm_source"], item["utm_campaign"]),
    )
    totals = {
        "report_views": sum(item["report_views"] for item in days_out),
        "role_views": sum(item["role_views"] for item in days_out),
        "checkout_starts": sum(item["checkout_starts"] for item in days_out),
        "snapshot_signups": snap_total,
        "waitlist_signups": wait_total,
    }
    return {
        "timezone": "UTC",
        "days": days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "note": (
            "Daily totals are stored in SQLite on the persistent disk "
            "(DATABASE_PATH, /data on Render) and survive redeploy. "
            "Each row is a UTC day, a path group, a role slug when the page is a role, "
            "and sanitized utm_source, utm_medium, utm_campaign, and utm_content. "
            "No IP address, user agent, or account id is stored. "
            "/admin/funnel groups the career-report funnel by src (card, lab, home), not by UTM."
        ),
        "counted": {
            "report_views": "GET 200 HTML for /reports, /reports/sample, /reports/success, and /reports/{id} (the id is not stored).",
            "role_views": "GET 200 HTML for /roles and /roles/{slug}.",
            "checkout_starts": "POST /api/reports/checkout that returns 200 after a Checkout Session is created.",
            "snapshot_signups": "Rows saved in role_snapshot_signups.",
            "waitlist_signups": "Rows saved in product_waitlist (MindGuild Pro and Career Radar).",
        },
        "excluded": [
            "HEAD, non-GET page loads, and responses that are not HTTP 200 HTML",
            "PDF downloads and other non-HTML responses",
            "404 and other error pages",
            "/healthz, /health, /readyz, /livez",
            "/admin and /admin/*",
            "obvious crawler and script user agents (the same list as the funnel)",
            "the app's own waitlist and report delivery user agents",
            "the in-process simulator and prune job, which do not request these pages",
            "Payment Link clicks on buy.stripe.com, which never reach this server",
        ],
        "utm": {
            "cookie": "mg_utm",
            "max_age_seconds": _COOKIE_MAX_AGE,
            "first_touch": True,
            "pattern": "[A-Za-z0-9._~-]{1,64}",
        },
        "totals": totals,
        "by_day": days_out,
        "roles": [item for item in roles if item["slug"] != "index"],
        "by_utm": {
            "views": _utm_rows(view_utm, ("report_views", "role_views", "count")),
            "snapshot_signups": _utm_rows(snap_utm, ("count",)),
            "waitlist_signups": wait_list,
            "checkout_starts": _utm_rows(checkout_utm, ("count",)),
        },
    }


def _has_table(c, table):
    if not _IDENT.match(table or ""):
        return False
    row = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def compact(c, days=14, now=None):
    """Short totals for /api/admin/db."""
    payload = summary(c, days=days, now=now)
    today = payload["until"]
    today_row = next((item for item in payload["by_day"] if item["day"] == today), None)
    return {
        "timezone": payload["timezone"],
        "days": payload["days"],
        "since": payload["since"],
        "until": payload["until"],
        "totals": payload["totals"],
        "today": None if today_row is None else {
            "day": today_row["day"],
            "report_views": today_row["report_views"],
            "role_views": today_row["role_views"],
            "checkout_starts": today_row["checkout_starts"],
        },
    }


def _with_ops(c, payload, now=None):
    import sim_budget
    import app as appmod

    moment = time.time() if now is None else now
    payload["sim_budget"] = sim_budget.snapshot(c, moment)
    payload["database"] = {
        "path": appmod.DB,
        "size_bytes": sim_budget.file_bytes(appmod.DB),
    }
    return payload


def _csv_safe(value):
    if value is None:
        return ""
    text = str(value)
    if text[:1] in _CSV_PREFIX:
        return "'" + text
    return text


def csv_body(c, days=14, now=None):
    days, since, until, start_ts, end_ts = _window(days, now)
    ensure(c)
    for table in ("role_snapshot_signups", "product_waitlist"):
        if _has_table(c, table):
            ensure_utm_columns(c, table)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "kind", "day", "path_group", "path_key",
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "count",
    ])
    rows = c.execute(
        """SELECT day, path_group, path_key, utm_source, utm_medium, utm_campaign, utm_content, hits
           FROM traffic_daily
           WHERE day >= ? AND day <= ?
           ORDER BY day, path_group, path_key, utm_source, utm_campaign""",
        (since.isoformat(), until.isoformat()),
    ).fetchall()
    for row in rows:
        kind = {
            "reports": "report_view",
            "roles": "role_view",
            "checkout": "checkout_start",
        }.get(row["path_group"], row["path_group"])
        writer.writerow([
            kind, row["day"], row["path_group"], _csv_safe(row["path_key"]),
            _csv_safe(row["utm_source"]), _csv_safe(row["utm_medium"]),
            _csv_safe(row["utm_campaign"]), _csv_safe(row["utm_content"]),
            int(row["hits"]),
        ])
    snaps = []
    if _has_table(c, "role_snapshot_signups"):
        snaps = c.execute(
        """SELECT date(created, 'unixepoch') AS day, role_id,
                  COALESCE(utm_source, ''), COALESCE(utm_medium, ''),
                  COALESCE(utm_campaign, ''), COALESCE(utm_content, ''),
                  COUNT(*) AS n
           FROM role_snapshot_signups
           WHERE created >= ? AND created < ?
           GROUP BY 1, 2, 3, 4, 5, 6
           ORDER BY 1, 2""",
        (start_ts, end_ts),
    ).fetchall()
    for row in snaps:
        writer.writerow([
            "snapshot_signup", row[0] or "", "snapshot", _csv_safe(row[1]),
            _csv_safe(row[2]), _csv_safe(row[3]), _csv_safe(row[4]), _csv_safe(row[5]),
            int(row[6]),
        ])
    waits = []
    if _has_table(c, "product_waitlist"):
        waits = c.execute(
        """SELECT date(created, 'unixepoch') AS day, product,
                  COALESCE(utm_source, ''), COALESCE(utm_medium, ''),
                  COALESCE(utm_campaign, ''), COALESCE(utm_content, ''),
                  COUNT(*) AS n
           FROM product_waitlist
           WHERE created >= ? AND created < ?
           GROUP BY 1, 2, 3, 4, 5, 6
           ORDER BY 1, 2""",
        (start_ts, end_ts),
    ).fetchall()
    for row in waits:
        writer.writerow([
            "waitlist_signup", row[0] or "", "waitlist", _csv_safe(row[1]),
            _csv_safe(row[2]), _csv_safe(row[3]), _csv_safe(row[4]), _csv_safe(row[5]),
            int(row[6]),
        ])
    return buf.getvalue()


def _private():
    return {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


def _load(days, now=None):
    flush()
    import app as appmod
    c = appmod.db()
    try:
        payload = summary(c, days=days, now=now)
        return _with_ops(c, payload, now=now)
    finally:
        c.close()


def admin_traffic(request: Request, authorization: Optional[str] = Header(None), days: int = 14):
    import app as appmod

    appmod.auth_admin(authorization)
    wanted = days if request.query_params.get("days") not in (None, "") else 14
    payload = _load(_clamp_days(wanted))
    return JSONResponse(payload, headers=_private())


def admin_traffic_csv(request: Request, authorization: Optional[str] = Header(None), days: int = 14):
    import app as appmod

    appmod.auth_admin(authorization)
    wanted = days if request.query_params.get("days") not in (None, "") else 14
    flush()
    c = appmod.db()
    try:
        body = csv_body(c, days=_clamp_days(wanted))
    finally:
        c.close()
    headers = _private()
    headers["Content-Disposition"] = 'attachment; filename="mindguild-traffic.csv"'
    headers["X-Content-Type-Options"] = "nosniff"
    return Response(body, media_type="text/csv; charset=utf-8", headers=headers)


def utm_js():
    return FileResponse(os.path.join(_STATIC, "utm.js"), media_type="text/javascript")


def admin_page():
    return FileResponse(os.path.join(_STATIC, "admin-traffic.html"), headers=_private())


def install(application):
    """Routes, the counter middleware, and the traffic_daily table."""
    global _installed
    if _installed:
        return
    import atexit
    import app as appmod

    c = appmod.db()
    try:
        ensure(c)
        c.commit()
    finally:
        c.close()
    application.add_api_route(
        "/api/admin/traffic",
        admin_traffic,
        methods=["GET"],
        tags=["admin"],
        summary="Daily report views, role views, checkout starts, and UTM totals (admin)",
    )
    application.add_api_route(
        "/api/admin/traffic.csv",
        admin_traffic_csv,
        methods=["GET"],
        tags=["admin"],
        summary="CSV of daily traffic and UTM totals (admin)",
    )
    application.add_api_route("/admin/traffic", admin_page, methods=["GET"], include_in_schema=False)
    application.add_api_route("/utm.js", utm_js, methods=["GET"], include_in_schema=False)
    application.add_middleware(TrafficMiddleware)
    atexit.register(flush)
    _installed = True
