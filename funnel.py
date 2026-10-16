"""First-party counts for the AI Career Report funnel.

Rows live in the same SQLite file as the rest of the site. Render has no
persistent disk, so these counts reset on every deploy. Nothing here stores
an IP address, a cookie, a user agent, or other personal data.

record_event(event, src=None, report_id=None) writes one row unless the
current request's User-Agent is an obvious bot. purchase_complete is stored
once per report id, including when both the webhook and the success page
run fulfillment.
"""

import os
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

EVENTS = ("reports_view", "checkout_start", "purchase_complete", "pro_waitlist_signup")
SOURCES = ("card", "lab", "home")
# Placement Payment Links. Kept out of SOURCES so the career-report summary
# still pre-seeds only card, lab, and home. clean_src stores these uppercase.
PLACEMENT_SOURCES = frozenset({
    "FEATURED_LINK_WEEK",
    "FEATURED_LINK_MONTH",
    "AD_LINK_DAY",
    "AD_LINK_WEEK",
    "AD_LINK_SECTION",
})
WINDOW_DAYS = 30
_WINDOW_SECONDS = WINDOW_DAYS * 24 * 60 * 60

# Obvious crawlers and scripted clients. "Googlebot" matches; "testclient" does not.
_BOT_UA = re.compile(
    r"bot\b|spider|crawl|slurp|wget|curl/|python-requests|python-urllib|"
    r"go-http-client|scrapy|headlesschrome|phantomjs|facebookexternalhit",
    re.I,
)
_SRC = re.compile(r"^(?:card|lab|home)$")
# Same shape as a career-report id. Shorter or stranger values are dropped.
_REPORT_ID = re.compile(r"^[A-Za-z0-9_-]{20,80}$")

_ua: ContextVar[str] = ContextVar("funnel_ua", default="")
_installed = False
_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "report-cta.css")


def note_user_agent(value):
    """Remember the User-Agent for record_event on this request. Returns a reset token."""
    return _ua.set("" if value is None else str(value))


def reset_user_agent(token):
    _ua.reset(token)


def is_bot_user_agent(value):
    return _BOT_UA.search(value or "") is not None


def clean_src(src):
    if src is None:
        return None
    text = str(src).strip()
    lower = text.lower()
    if _SRC.match(lower):
        return lower
    upper = text.upper()
    if upper in PLACEMENT_SOURCES:
        return upper
    return None


def clean_report_id(report_id):
    if report_id is None:
        return None
    text = str(report_id).strip()
    if _REPORT_ID.match(text):
        return text
    return None


def ensure_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS funnel_events(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        event TEXT NOT NULL,
        src TEXT,
        report_id TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_funnel_events_ts ON funnel_events(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_funnel_events_report ON funnel_events(report_id, event)")
    c.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_funnel_purchase
           ON funnel_events(report_id)
           WHERE event='purchase_complete' AND report_id IS NOT NULL"""
    )


class FunnelUserAgentMiddleware:
    """Keep the request User-Agent where record_event can see it. The value is not stored."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = ""
        for key, value in scope.get("headers") or ():
            if key.lower() == b"user-agent":
                raw = value.decode("latin-1", "replace")
                break
        token = note_user_agent(raw)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_user_agent(token)


def record_event(event, src=None, report_id=None):
    """Store one funnel row. Returns True when a new row was written.

    Skips obvious bots. purchase_complete is ignored when that report id
    already has one, so webhook and success-page fulfillment count once.
    A placement purchase has no report id; its src is the Payment Link tier.
    """
    if is_bot_user_agent(_ua.get()):
        return False
    if event not in EVENTS:
        return False
    src = clean_src(src)
    report_id = clean_report_id(report_id)
    if event == "purchase_complete" and not report_id and src not in PLACEMENT_SOURCES:
        return False
    try:
        return _insert(event, src, report_id)
    except Exception:
        print("funnel record failed", flush=True)
        return False


def _insert(event, src, report_id):
    import app as appmod

    c = appmod.db()
    try:
        ensure_table(c)
        if event == "purchase_complete" and src is None:
            prior = c.execute(
                """SELECT src FROM funnel_events
                   WHERE event='checkout_start' AND report_id=? AND src IS NOT NULL
                   ORDER BY id DESC LIMIT 1""",
                (report_id,),
            ).fetchone()
            if prior:
                src = clean_src(prior["src"])
        if event == "purchase_complete":
            cur = c.execute(
                "INSERT OR IGNORE INTO funnel_events(ts, event, src, report_id) VALUES(?,?,?,?)",
                (time.time(), event, src, report_id),
            )
        else:
            cur = c.execute(
                "INSERT INTO funnel_events(ts, event, src, report_id) VALUES(?,?,?,?)",
                (time.time(), event, src, report_id),
            )
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def _rate(numerator, denominator):
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _bucket():
    return {name: 0 for name in EVENTS}


def _with_rates(counts):
    return {
        "reports_view": counts["reports_view"],
        "checkout_start": counts["checkout_start"],
        "purchase_complete": counts["purchase_complete"],
        "view_to_checkout": _rate(counts["checkout_start"], counts["reports_view"]),
        "checkout_to_purchase": _rate(counts["purchase_complete"], counts["checkout_start"]),
    }


def _src_sort(src):
    if src in SOURCES:
        return (SOURCES.index(src), src)
    if src is None:
        return (len(SOURCES), "")
    return (len(SOURCES) + 1, src)


def _display_src(src):
    if src is None:
        return None
    return str(src)[:32]


def summary(now=None):
    """Counts for the last 30 UTC days, split by src and by day."""
    now = time.time() if now is None else now
    since = now - _WINDOW_SECONDS
    import app as appmod

    c = appmod.db()
    try:
        ensure_table(c)
        rows = c.execute(
            """SELECT date(ts, 'unixepoch') AS day, src, event, COUNT(*) AS n
               FROM funnel_events
               WHERE ts >= ? AND event IN ('reports_view', 'checkout_start', 'purchase_complete')
               GROUP BY day, src, event
               ORDER BY day""",
            (since,),
        ).fetchall()
    finally:
        c.close()

    totals = _bucket()
    by_src = {name: _bucket() for name in SOURCES}
    by_day = {}
    for row in rows:
        day = row["day"]
        event = row["event"]
        src = row["src"] if row["src"] in SOURCES else _display_src(row["src"])
        count = int(row["n"])
        if event not in totals:
            continue
        totals[event] += count
        by_src.setdefault(src, _bucket())
        by_src[src][event] += count
        day_row = by_day.setdefault(day, {"counts": _bucket(), "sources": {}})
        day_row["counts"][event] += count
        day_row["sources"].setdefault(src, _bucket())
        day_row["sources"][src][event] += count

    src_rows = []
    for src in sorted(by_src, key=_src_sort):
        item = _with_rates(by_src[src])
        item["src"] = src
        src_rows.append(item)

    day_rows = []
    for day in sorted(by_day):
        item = _with_rates(by_day[day]["counts"])
        item["day"] = day
        item["by_src"] = []
        for src in sorted(by_day[day]["sources"], key=_src_sort):
            part = _with_rates(by_day[day]["sources"][src])
            part["src"] = src
            item["by_src"].append(part)
        day_rows.append(item)

    start_day = datetime.fromtimestamp(since, timezone.utc).date().isoformat()
    end_day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    return {
        "window_days": WINDOW_DAYS,
        "since": start_day,
        "until": end_day,
        "timezone": "UTC",
        "note": "Counts reset on each deploy because there is no persistent disk.",
        "totals": _with_rates(totals),
        "by_src": src_rows,
        "by_day": day_rows,
    }


def _esc(value):
    import html
    return html.escape("" if value is None else str(value), quote=True)


def _pct(rate):
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _rate_label(counts, key, numerator, denominator):
    rate = counts[key]
    label = _pct(rate)
    if rate is None:
        return label
    return f"{label} ({counts[numerator]} of {counts[denominator]})"


def _src_label(src):
    if src is None:
        return "none"
    return str(src)


def render_admin(payload):
    totals = payload["totals"]

    def cells(counts):
        return (
            f"<td>{counts['reports_view']}</td>"
            f"<td>{counts['checkout_start']}</td>"
            f"<td>{counts['purchase_complete']}</td>"
            f"<td>{_esc(_rate_label(counts, 'view_to_checkout', 'checkout_start', 'reports_view'))}</td>"
            f"<td>{_esc(_rate_label(counts, 'checkout_to_purchase', 'purchase_complete', 'checkout_start'))}</td>"
        )

    src_body = []
    for row in payload["by_src"]:
        src_body.append(
            f"<tr><th scope=\"row\">{_esc(_src_label(row['src']))}</th>{cells(row)}</tr>"
        )
    if not src_body:
        src_body.append("<tr><td colspan=\"6\">No sources in this window.</td></tr>")

    day_body = []
    for day in payload["by_day"]:
        for row in day["by_src"]:
            day_body.append(
                "<tr>"
                f"<th scope=\"row\">{_esc(day['day'])}</th>"
                f"<td>{_esc(_src_label(row['src']))}</td>"
                f"{cells(row)}</tr>"
            )
    if not day_body:
        day_body.append("<tr><td colspan=\"7\">No events in the last 30 days.</td></tr>")

    head = (
        "<th scope=\"col\">Views</th><th scope=\"col\">Checkout starts</th>"
        "<th scope=\"col\">Purchases</th><th scope=\"col\">View to checkout</th>"
        "<th scope=\"col\">Checkout to purchase</th>"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Report funnel · MindGuild</title>
<style>
*{{box-sizing:border-box}}html{{color-scheme:dark}}
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f1220;color:#e7e9f3}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:1.6rem;margin:0 0 8px}}
p{{line-height:1.5}}
.h{{color:#c5cce6;font-size:14px}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 0 28px}}
table{{border-collapse:collapse;width:100%;min-width:640px}}
caption{{text-align:left;font-weight:700;margin:0 0 8px;font-size:1.05rem}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #262c48;font-size:14px;vertical-align:top}}
thead th{{font-size:12px;letter-spacing:.03em;text-transform:uppercase;color:#c5cce6}}
:focus-visible{{outline:2px solid #e4e8ff;outline-offset:2px}}
dl{{display:grid;gap:8px;margin:12px 0 28px}}
dt{{font-size:12px;letter-spacing:.03em;text-transform:uppercase;color:#c5cce6}}
dd{{margin:2px 0 0;font-size:1.1rem}}
a{{color:#b7c4ff}}
@media(max-width:640px){{main{{padding:16px 12px 32px}}h1{{font-size:1.35rem}}}}
</style></head><body>
<main>
<h1>AI Career Report funnel</h1>
<p class="h">{_esc(payload['note'])} Window: last {payload['window_days']} days ({_esc(payload['since'])} through {_esc(payload['until'])}, UTC). JSON: add <code>?format=json</code>.</p>
<dl>
<div><dt>Views</dt><dd>{totals['reports_view']}</dd></div>
<div><dt>Checkout starts</dt><dd>{totals['checkout_start']}</dd></div>
<div><dt>Purchases</dt><dd>{totals['purchase_complete']}</dd></div>
<div><dt>View to checkout</dt><dd>{_esc(_rate_label(totals, 'view_to_checkout', 'checkout_start', 'reports_view'))}</dd></div>
<div><dt>Checkout to purchase</dt><dd>{_esc(_rate_label(totals, 'checkout_to_purchase', 'purchase_complete', 'checkout_start'))}</dd></div>
</dl>
<div class="scroll" tabindex="0">
<table>
<caption>Counts by source</caption>
<thead><tr><th scope="col">Source</th>{head}</tr></thead>
<tbody>{''.join(src_body)}</tbody>
</table>
</div>
<div class="scroll" tabindex="0">
<table>
<caption>Counts by day and source</caption>
<thead><tr><th scope="col">Day (UTC)</th><th scope="col">Source</th>{head}</tr></thead>
<tbody>{''.join(day_body)}</tbody>
</table>
</div>
<p class="h">Source <code>none</code> is a visit with no <code>src</code> parameter. A dash means the rate has no denominator. No IP addresses, cookies, or user agents are stored.</p>
</main>
</body></html>"""


def admin_funnel(request: Request, authorization: Optional[str] = Header(None), format: str = ""):
    import app as appmod

    appmod.auth_admin(authorization)
    payload = summary()
    headers = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}
    wanted = format or request.query_params.get("format") or ""
    if wanted.strip().lower() == "json":
        return JSONResponse(payload, headers=headers)
    return HTMLResponse(render_admin(payload), headers=headers)


def report_cta_css():
    return FileResponse(_CSS, media_type="text/css")


def install(application):
    """Attach bot detection, the admin funnel page, and the shared CTA stylesheet."""
    global _installed
    if _installed:
        return
    application.add_middleware(FunnelUserAgentMiddleware)
    application.add_api_route(
        "/admin/funnel",
        admin_funnel,
        methods=["GET"],
        tags=["admin"],
        summary="Career-report funnel counts for the last 30 days (admin)",
        include_in_schema=False,
    )
    application.add_api_route(
        "/report-cta.css",
        report_cta_css,
        methods=["GET"],
        include_in_schema=False,
    )
    _installed = True
