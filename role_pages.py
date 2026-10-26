"""Public pages for the occupations in an AI Career Report, plus a free snapshot.

The roles are career_data.REAL_CAREERS. That list is what a personal report
can recommend. The speculative sketches in SPECULATIVE_CAREERS stay inside a
personal report; they do not get a page here.

A snapshot is built only from that role's curated entry: the summary, the
evidence label already used on career reports, the listed skills, courses,
and sources. It does not add pay, employment, or growth figures. The visitor
sees it on the role page after entering an email. No email is sent.

Signups live in role_snapshot_signups (email, role, source). ensure() is
CREATE TABLE IF NOT EXISTS, additive columns, and indexes. It does not touch
posts or the search index, so startup does not rebuild FTS.

Raw IP addresses are not stored. The rate limit uses app.client_ip, which is
the socket peer, or CF-Connecting-IP when RENDER is set.
"""

import csv
import io
import os
import re
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qs

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

import career_data

SIGNUP_LIMIT = (5, 3600)
DELIVERY_NOTE = (
    "MindGuild shows this snapshot on this page after you submit your email. "
    "No email is sent. MindGuild does not email snapshots unless it sets up sending later."
)
OUTLOOK = (
    "This snapshot does not copy salary, employment, or growth figures. "
    "When those figures exist, they stay on the source pages linked below. "
    "This is not a hiring forecast and not a local labor-market series."
)
_EMAIL = re.compile(r"\A[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}\Z")
_SOURCE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_COLUMNS = (
    ("email", "TEXT"),
    ("role_id", "TEXT"),
    ("source", "TEXT"),
    ("created", "REAL"),
)
_CSV_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")
_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_FILTER_JS = """<script>
(function () {
  var input = document.getElementById("q");
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-role]"));
  var empty = document.getElementById("none");
  var count = document.getElementById("role-count");
  if (!input) return;
  function apply() {
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    items.forEach(function (el) {
      var hay = el.getAttribute("data-search") || "";
      var ok = !q || hay.indexOf(q) !== -1;
      el.hidden = !ok;
      if (ok) shown += 1;
    });
    if (empty) empty.hidden = shown !== 0;
    if (count) count.textContent = q ? (shown + " of " + items.length + " roles") : (items.length + " roles");
  }
  input.addEventListener("input", apply);
  apply();
})();
</script>"""


def esc(value):
    return escape("" if value is None else str(value), quote=True)


def roles():
    """Occupations a career report can recommend, in catalog order."""
    return list(career_data.REAL_CAREERS)


def public_role_paths():
    return tuple("/roles/" + entry["id"] for entry in roles())


def role_by_id(slug):
    if not isinstance(slug, str):
        return None
    for entry in roles():
        if entry["id"] == slug:
            return entry
    return None


def _cols(c, table):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _ensure_column(c, table, column, decl):
    if column not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure(c):
    """Create the signup table if it is missing. Safe to call more than once.

    Posts and the search index are left alone.
    """
    c.execute("""CREATE TABLE IF NOT EXISTS role_snapshot_signups(
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        role_id TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'roles',
        created REAL NOT NULL)""")
    for column, decl in _COLUMNS:
        _ensure_column(c, "role_snapshot_signups", column, decl)
    c.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_role_snapshot_email_role
           ON role_snapshot_signups(email, role_id)"""
    )
    c.execute(
        """CREATE INDEX IF NOT EXISTS ix_role_snapshot_role
           ON role_snapshot_signups(role_id, created)"""
    )
    import traffic
    traffic.ensure_utm_columns(c, "role_snapshot_signups")


def _email(value):
    if not isinstance(value, str):
        raise HTTPException(422, "Enter a valid email")
    text = value.strip().lower()
    if not text or len(text) > 254 or not _EMAIL.match(text):
        raise HTTPException(422, "Enter a valid email")
    return text


def clean_source(value):
    """A short label for where the signup came from. Junk becomes 'roles'."""
    if not isinstance(value, str):
        return "roles"
    text = value.strip()
    if _SOURCE.match(text):
        return text
    return "roles"


def clean_query(value):
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def _reject_honeypot(value):
    if value is None:
        return
    if not isinstance(value, str) or value.strip():
        raise HTTPException(422, "Could not save that signup.")


def _haystack(entry):
    parts = [entry["id"], entry["title"], entry["label"], entry["summary"]]
    parts.extend(entry.get("skills") or [])
    return " ".join(parts).casefold()


def _matches(entry, query):
    if not query:
        return True
    return query.casefold() in _haystack(entry)


def _badge_class(label):
    if label == "Emerging research":
        return "emerging"
    if label == "Speculative":
        return "speculative"
    return "established"


def snapshot_for(entry):
    """One role's snapshot. Every field comes from the curated career entry."""
    import reports

    label = entry["label"]
    gloss = reports.LABEL_GLOSS[label]
    courses = []
    for cid in entry["course_ids"]:
        course = career_data.COURSES[cid]
        courses.append({
            "title": course["title"],
            "provider": course["provider"],
            "url": course["url"],
        })
    speculative = label == "Speculative"
    return {
        "id": entry["id"],
        "title": entry["title"],
        "label": label,
        "gloss": gloss,
        "speculative": speculative,
        "disclaimer": career_data.SPECULATIVE_DISCLAIMER if speculative else None,
        "summary": entry["summary"],
        "outlook": OUTLOOK,
        "skills": list(entry["skills"]),
        "courses": courses,
        "sources": [{"name": source["name"], "url": source["url"]} for source in entry["sources"]],
        "delivery": DELIVERY_NOTE,
    }


def _store(email, role_id, source, utm=None):
    import time
    import app as appmod
    import traffic

    now = time.time()
    stored = traffic.column_values(utm)
    c = appmod.db()
    try:
        ensure(c)
        cur = c.execute(
            """INSERT OR IGNORE INTO role_snapshot_signups(
                   email, role_id, source, created,
                   utm_source, utm_medium, utm_campaign, utm_content)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, role_id, source, now, *stored),
        )
        created = cur.rowcount == 1
        c.commit()
    finally:
        c.close()
    return created


async def _body(request: Request):
    raw = await request.body()
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        import json
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(422, "Send a JSON object with an email.")
        if not isinstance(data, dict):
            raise HTTPException(422, "Send a JSON object with an email.")
        return data, "json"
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}, "form"


def _take_signup(data, entry, request=None):
    _reject_honeypot(data.get("website"))
    email = _email(data.get("email"))
    source = clean_source(data.get("source") if data.get("source") not in (None, "") else data.get("src"))
    utm = None
    try:
        import traffic
        utm = traffic.parts_from(request, data)
    except Exception:
        utm = None
    created = _store(email, entry["id"], source, utm)
    return email, created, snapshot_for(entry)


def _meta(text, limit=180):
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _shell(title, main, description, *, current=False, extra=""):
    mark = ' aria-current="page"' if current else ""
    desc = esc(description)
    safe_title = esc(title)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%A4%96%3C/text%3E%3C/svg%3E">
<title>{safe_title} · MindGuild</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{safe_title} · MindGuild">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="/og.png">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title} · MindGuild">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="/og.png">
<style>
*{{box-sizing:border-box}}html{{color-scheme:dark}}body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f1220;color:#e7e9f3;overflow-x:clip}}
.skip{{position:absolute;left:12px;top:8px;transform:translateY(-160%);background:#7c8cff;color:#0f1220;padding:8px 14px;border-radius:8px;z-index:40;font-weight:700;text-decoration:none}}
.skip:focus,.skip:focus-visible{{transform:none}}
:focus-visible{{outline:2px solid #e4e8ff;outline-offset:2px}}
header{{background:#151a2e;border-bottom:1px solid #262c48;padding:14px 20px;display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap}}
a{{color:#b7c4ff}}header .brand{{color:inherit;text-decoration:none}}
header .logo{{font-size:20px;font-weight:700;color:#e7e9f3;line-height:1.2}}header .logo span{{color:#7c8cff}}
header .tagline{{margin:0}}
header nav{{display:flex;flex-wrap:wrap;align-items:center;gap:2px 12px;margin-left:auto}}
header nav a{{min-height:44px;display:inline-flex;align-items:center;padding:0 8px;text-decoration:none}}header nav a[aria-current=page]{{font-weight:700;text-decoration:underline;text-underline-offset:4px}}
.h{{color:#c5cce6;font-size:13px}}
main{{max-width:800px;margin:24px auto;padding:0 16px}}
.card{{background:#171c33;border:1px solid #262c48;border-radius:14px;padding:22px 24px;margin-bottom:16px;line-height:1.55;overflow-wrap:anywhere}}
h1{{margin:0 0 8px;font-size:28px;letter-spacing:-.02em}}h2{{margin:8px 0;font-size:20px}}h3{{margin:16px 0 6px;font-size:14px;letter-spacing:.04em;text-transform:uppercase;color:#c5cce6}}
.badge{{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;border-radius:999px;padding:3px 10px;margin:0}}
.badge.established{{background:#163226;color:#b6f3cf}}
.badge.emerging{{background:#1c2448;color:#c9d4ff}}
.badge.speculative{{background:#2a2516;color:#ffd98a;border:1px solid #4a3f1c}}
.disclaimer{{color:#ffd98a;font-weight:700;margin:8px 0}}
.gloss{{color:#c5cce6;margin-top:0}}
.note{{color:#ffd98a;background:#2a2516;border:1px solid #4a3f1c;border-radius:10px;padding:10px 12px}}
.err{{color:#ffc1cb;font-size:14px;margin:8px 0 0}}
label{{display:block;font-weight:600;margin:12px 0 4px}}
input{{width:100%;max-width:420px;background:#0b0e1a;color:#e7e9f3;border:1px solid #39407a;border-radius:10px;padding:10px 12px;font:inherit;font-size:16px;min-height:44px}}
.btn{{display:inline-flex;align-items:center;min-height:44px;background:#7c8cff;color:#0f1220;font-weight:700;border:0;border-radius:999px;padding:10px 18px;text-decoration:none;cursor:pointer;font:inherit;font-size:15px}}
.search{{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin:12px 0 0}}
.search label{{margin:0 0 4px;flex-basis:100%}}
.hp{{position:absolute;left:-10000px;top:auto;width:1px;height:1px;overflow:hidden}}
ul,ol{{padding-left:1.2em}}li{{margin:6px 0}}
footer{{overflow-wrap:anywhere}}
@media(max-width:768px){{header{{padding:10px 12px}}header .tagline{{display:none}}header nav{{flex-basis:100%;margin-left:0}}.card{{padding:16px}}h1{{font-size:24px}}}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="/" aria-label="MindGuild home"><span class="logo">🤖 Mind<span>Guild</span></span></a><p class="tagline h">AI jobs, one role at a time</p>
<nav aria-label="Site"><a href="/roles"{mark}>Roles</a><a href="/reports">Reports</a><a href="/requests">Requests</a><a href="/pricing">Pricing</a><a href="/community">Community</a></nav></header>
<main id="main" tabindex="-1">{main}</main>
<footer style="text-align:center;color:#c5cce6;font-size:13px;padding:24px 16px 32px"><a href="/rules">Community Rules</a> · <a href="/join">Join</a> · <a href="/partners">Partners</a> · <a href="/pricing">Pricing</a> · <a href="/about">About</a> · <a href="/ads">Ads</a> · <a href="/requests">Requests</a> · <a href="/roles">Roles</a> · <a href="/docs">API docs</a> · <a href="/reports">Career reports</a> · <a href="/refunds">Refunds</a> · <a href="/community">Community</a> · <a href="/search">Search</a> · <a href="/login">Log in</a> · <a href="/signup">Sign up</a> · <a href="/messages">Messages</a> · <a href="/me/bots">My Bots</a></footer>
<script src="/utm.js" defer></script>
{extra}</body></html>"""


def _snapshot_html(snap, *, created):
    badge = _badge_class(snap["label"])
    skills = "".join(f"<li>{esc(skill)}</li>" for skill in snap["skills"])
    courses = "".join(
        "<li><a href=\"{url}\" rel=\"noopener noreferrer\">{title}</a> "
        "<span class=\"h\">{provider}</span></li>".format(
            url=esc(course["url"]),
            title=esc(course["title"]),
            provider=esc(course["provider"]),
        )
        for course in snap["courses"]
    )
    sources = "".join(
        f"<li><a href=\"{esc(source['url'])}\" rel=\"noopener noreferrer\">{esc(source['name'])}</a></li>"
        for source in snap["sources"]
    )
    disclaimer = ""
    if snap.get("disclaimer"):
        disclaimer = f"<p class=\"disclaimer\">{esc(snap['disclaimer'])}</p>"
    again = ""
    if not created:
        again = "<p>This email was already saved for this role. Here is the snapshot again.</p>"
    return (
        f"<article class=\"card\" id=\"snapshot\">"
        f"<h2>Your free snapshot</h2>"
        f"{again}"
        f"<p class=\"badge {badge}\">{esc(snap['label'])}</p>"
        f"{disclaimer}"
        f"<p class=\"gloss\">{esc(snap['gloss'])}</p>"
        f"<h3>What AI work looks like in this role</h3><p>{esc(snap['summary'])}</p>"
        f"<h3>Outlook</h3><p class=\"note\">{esc(snap['outlook'])}</p>"
        f"<h3>Skills this career report lists</h3><ul>{skills}</ul>"
        f"<h3>Courses this career report lists</h3><ul>{courses}</ul>"
        f"<h3>Sources</h3><ul>{sources}</ul>"
        f"<p class=\"h\">{esc(snap['delivery'])}</p>"
        f"</article>"
    )


def _signup_form(entry, source, error=""):
    err = f"<p class=\"err\" role=\"alert\">{esc(error)}</p>" if error else ""
    return f"""<article class="card" id="snapshot-signup">
<h2>Free snapshot</h2>
<p id="snapshot-note">{esc(DELIVERY_NOTE)}</p>
<form method="post" action="/roles/{esc(entry['id'])}">
<label for="email">Email</label>
<input id="email" name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com" aria-describedby="snapshot-note">
<div class="hp" aria-hidden="true">
<label for="website">Website</label>
<input id="website" name="website" type="text" tabindex="-1" autocomplete="off">
</div>
<input type="hidden" name="source" value="{esc(source)}">
{__import__('traffic').hidden_inputs()}
<button class="btn" type="submit">Show my free snapshot</button>
{err}
</form>
</article>"""


def render_index(query):
    catalog = roles()
    shown = 0
    cards = []
    for entry in catalog:
        match = _matches(entry, query)
        if match:
            shown += 1
        hidden = "" if match else " hidden"
        badge = _badge_class(entry["label"])
        cards.append(
            f"<article class=\"card\" data-role=\"{esc(entry['id'])}\" data-search=\"{esc(_haystack(entry))}\"{hidden}>"
            f"<p class=\"badge {badge}\">{esc(entry['label'])}</p>"
            f"<h2><a href=\"/roles/{esc(entry['id'])}\">{esc(entry['title'])}</a></h2>"
            f"<p>{esc(entry['summary'])}</p>"
            f"</article>"
        )
    empty_hidden = "" if shown == 0 else " hidden"
    if query:
        count_text = f"{shown} of {len(catalog)} roles"
    else:
        count_text = f"{len(catalog)} roles"
    main = f"""<article class="card">
<h1>Roles</h1>
<p>These are the occupations MindGuild's AI Career Report can recommend. Each page is that role's summary from the same curated list, with a link to the full report. A free on-page snapshot of the role: what AI work in it involves, the skills, courses and sources. No score, pay or growth figures. {esc(DELIVERY_NOTE)} Pay and employment figures are not copied.</p>
<form class="search" method="get" action="/roles" role="search">
<label for="q">Search roles</label>
<input id="q" name="q" type="search" value="{esc(query)}" maxlength="80" autocomplete="off">
<button class="btn" type="submit">Filter</button>
</form>
<p class="h" id="role-count">{count_text}</p>
</article>
<p id="none" class="card"{empty_hidden}>No roles match that search.</p>
{''.join(cards)}"""
    description = (
        "Occupations from MindGuild AI Career Reports. "
        "A free on-page snapshot of the role: what AI work in it involves, the skills, courses and sources. "
        "No score, pay or growth figures."
    )
    return _shell("Roles", main, description, current=True, extra=_FILTER_JS)


def render_role(entry, source, *, snap=None, created=False, error=""):
    badge = _badge_class(entry["label"])
    snapshot = _snapshot_html(snap, created=created) if snap else ""
    main = f"""<p><a href="/roles">All roles</a></p>
<article class="card">
<p class="badge {badge}">{esc(entry['label'])}</p>
<h1>{esc(entry['title'])}</h1>
<p class="gloss">{esc(snapshot_for(entry)['gloss'])}</p>
<p>{esc(entry['summary'])}</p>
<p><a class="btn" href="/reports">Get the full AI Career Report</a></p>
<p class="h">The full report maps what you enter onto curated occupations and courses, including this one when it matches. This page is one occupation from that list. Pay and employment figures stay on the source pages.</p>
</article>
{snapshot}
{_signup_form(entry, source, error)}"""
    description = _meta(entry["title"] + ". " + entry["label"] + ". " + entry["summary"])
    return _shell(entry["title"], main, description, current=True)


def render_missing():
    main = """<article class="card"><h1>Role not found</h1>
<p>That address is not one of the occupations in the AI Career Report.</p>
<p><a href="/roles">Browse roles</a></p></article>"""
    return _shell("Role not found", main, "That role is not in the MindGuild AI Career Report.")


def _html(body, status=200):
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _title_for(role_id):
    entry = role_by_id(role_id)
    if entry:
        return entry["title"]
    return role_id


def summary(c):
    ensure(c)
    found = {
        row["role_id"]: int(row["n"])
        for row in c.execute(
            "SELECT role_id, COUNT(*) AS n FROM role_snapshot_signups GROUP BY role_id"
        )
    }
    counts = []
    seen = set()
    for entry in roles():
        seen.add(entry["id"])
        counts.append({
            "role_id": entry["id"],
            "title": entry["title"],
            "signups": found.get(entry["id"], 0),
        })
    for role_id in sorted(found):
        if role_id not in seen:
            counts.append({
                "role_id": role_id,
                "title": role_id,
                "signups": found[role_id],
            })
    signups = []
    for row in c.execute(
        """SELECT id, email, role_id, source, created
           FROM role_snapshot_signups ORDER BY created DESC, id DESC"""
    ):
        signups.append({
            "id": row["id"],
            "email": row["email"],
            "role_id": row["role_id"],
            "title": _title_for(row["role_id"]),
            "source": row["source"],
            "created": row["created"],
        })
    return {
        "note": DELIVERY_NOTE + " Raw IP addresses are not stored.",
        "total": sum(item["signups"] for item in counts),
        "counts": counts,
        "signups": signups,
    }


def _csv_safe(value):
    if value is None:
        return ""
    text = str(value)
    if text[:1] in _CSV_PREFIX:
        return "'" + text
    return text


def _created_iso(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def csv_body(c):
    ensure(c)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "email", "role_id", "role_title", "source", "created_utc"])
    rows = c.execute(
        """SELECT id, email, role_id, source, created
           FROM role_snapshot_signups ORDER BY created ASC, id ASC"""
    ).fetchall()
    for row in rows:
        writer.writerow([
            row["id"],
            _csv_safe(row["email"]),
            _csv_safe(row["role_id"]),
            _csv_safe(_title_for(row["role_id"])),
            _csv_safe(row["source"]),
            _created_iso(row["created"]),
        ])
    return buf.getvalue()


def _private():
    return {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


def register(application):
    import app as appmod

    appmod.LIMITS["role_snapshot"] = SIGNUP_LIMIT
    c = appmod.db()
    try:
        ensure(c)
        c.commit()
    finally:
        c.close()

    @application.get("/roles", include_in_schema=False)
    def roles_index(q: str = ""):
        return HTMLResponse(render_index(clean_query(q)))

    @application.get("/roles/{slug}", include_in_schema=False)
    def role_page(slug: str, request: Request):
        entry = role_by_id(slug)
        if not entry:
            return _html(render_missing(), 404)
        source = clean_source(request.query_params.get("src") or request.query_params.get("source"))
        return HTMLResponse(render_role(entry, source))

    @application.post("/roles/{slug}", include_in_schema=False)
    async def role_signup(slug: str, request: Request):
        entry = role_by_id(slug)
        if not entry:
            return _html(render_missing(), 404)
        appmod.rate("role_snapshot", appmod.client_ip(request))
        data, kind = await _body(request)
        try:
            _email_value, created, snap = _take_signup(data, entry, request)
        except HTTPException as exc:
            if kind == "json":
                raise
            detail = exc.detail if isinstance(exc.detail, str) else "Could not save that signup."
            source = clean_source(data.get("source") or data.get("src"))
            return _html(render_role(entry, source, error=detail), exc.status_code)
        source = clean_source(data.get("source") or data.get("src"))
        if kind == "json":
            return JSONResponse(
                {"ok": True, "created": created, "snapshot": snap},
                status_code=201 if created else 200,
                headers={"Cache-Control": "no-store"},
            )
        return _html(render_role(entry, source, snap=snap, created=created))

    @application.post(
        "/api/roles/{slug}/snapshot",
        tags=["roles"],
        summary="Save an email and return one role's free snapshot. No email is sent.",
    )
    async def role_snapshot_api(slug: str, request: Request):
        entry = role_by_id(slug)
        if not entry:
            raise HTTPException(404, "Role not found")
        appmod.rate("role_snapshot", appmod.client_ip(request))
        data, _kind = await _body(request)
        if not isinstance(data, dict):
            raise HTTPException(422, "Send a JSON object with an email.")
        _email_value, created, snap = _take_signup(data, entry, request)
        return JSONResponse(
            {"ok": True, "created": created, "snapshot": snap},
            status_code=201 if created else 200,
            headers={"Cache-Control": "no-store"},
        )

    @application.get(
        "/api/admin/snapshots",
        tags=["admin"],
        summary="Role snapshot signup counts and rows (admin)",
    )
    def admin_snapshots(_actor: dict = Depends(appmod.auth_admin)):
        c = appmod.db()
        try:
            payload = summary(c)
            c.commit()
        finally:
            c.close()
        return JSONResponse(payload, headers=_private())

    @application.get(
        "/api/admin/snapshots.csv",
        tags=["admin"],
        summary="CSV of role snapshot signups (admin)",
    )
    def admin_snapshots_csv(_actor: dict = Depends(appmod.auth_admin)):
        c = appmod.db()
        try:
            body = csv_body(c)
            c.commit()
        finally:
            c.close()
        headers = _private()
        headers["Content-Disposition"] = 'attachment; filename="mindguild-role-snapshots.csv"'
        headers["X-Content-Type-Options"] = "nosniff"
        return Response(body, media_type="text/csv; charset=utf-8", headers=headers)

    @application.get("/admin/snapshots", include_in_schema=False)
    def admin_snapshots_page():
        return FileResponse(
            os.path.join(_STATIC, "admin-snapshots.html"),
            headers=_private(),
        )
