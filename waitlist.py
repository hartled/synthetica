"""Shared MindGuild Pro and Career Radar waitlists.

Both products are not built yet. Signup stores an email, the product, an
optional Career Radar price pick ($12, $19, or $29), and the source page.
Nothing here charges a card or sends email.

pro_waitlist stays the Pro table the pricing API already used. Each new Pro
signup is written there and into product_waitlist. Career Radar uses
product_waitlist only. ensure() is CREATE TABLE IF NOT EXISTS, additive
columns, indexes, and a copy of existing Pro rows. It does not touch posts
or the search index.

Raw IP addresses are not stored. Rate limits use app.client_ip, which is the
socket peer, or CF-Connecting-IP when RENDER is set.
"""

import csv
import io
import os
import re
import time
from datetime import datetime, timezone
from html import escape

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

# Five signup attempts per hour per IP, shared by Pro and Career Radar.
SIGNUP_LIMIT = (5, 3600)
# Public pages omit the count until this many real rows exist.
PUBLIC_COUNT_MIN = 10
PRODUCTS = ("pro", "career_radar")
PRODUCT_LABELS = {"pro": "MindGuild Pro", "career_radar": "Career Radar"}
PRICE_OPTIONS = (12, 19, 29)
SOURCES = ("pricing", "reports", "sample", "report")
SOURCE_LABELS = {
    "pricing": "/pricing",
    "reports": "/reports",
    "sample": "/reports/sample",
    "report": "Career report",
}
NOTE = (
    "MindGuild Pro and Career Radar are not built yet. "
    "Joining a waitlist does not charge anyone, and no payment is taken. "
    "Counts are stored signups. Raw IP addresses are not stored."
)

_EMAIL = re.compile(r"\A[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}\Z")
_COLUMNS = (
    ("email", "TEXT"),
    ("product", "TEXT"),
    ("price_usd", "INTEGER"),
    ("source", "TEXT"),
    ("created", "REAL"),
)
_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_CSV_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")

_STYLE = """<style>
.waitlist-card{border-style:dashed}
.waitlist-card h2{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:0 0 8px;font-size:20px}
.wl-badge{display:inline-block;font-size:11px;font-weight:700;background:#2d3560;color:#d5dcff;border-radius:999px;padding:3px 10px;text-transform:uppercase;letter-spacing:.04em}
.waitlist-card p{margin:8px 0}
.waitlist-card label{font-weight:600}
.waitlist-card input[type=email]{width:100%;max-width:360px;background:#0b0e1a;color:#e7e9f3;border:1px solid #39407a;border-radius:8px;padding:8px 10px;font:inherit;font-size:16px;min-height:44px}
.waitlist-card .wl-prices{border:1px solid #262c48;border-radius:12px;margin:12px 0;padding:8px 14px 14px}
.waitlist-card .wl-prices label{display:inline-flex;align-items:center;gap:6px;min-height:44px;margin-right:12px;font-weight:600}
.waitlist-card button.btn{min-height:44px;background:#7c8cff;color:#0f1220;font-weight:700;border:0;border-radius:999px;padding:10px 18px;cursor:pointer;font:inherit;font-size:15px}
.wl-msg{min-height:1.2em;margin:8px 0 0}
.wl-msg.ok{color:#9be5c4}
.wl-msg.err{color:#ffb4c0}
.wl-count{color:#c5cce6;font-size:14px}
.waitlist-card .hp{position:absolute;left:-10000px;top:auto;width:1px;height:1px;overflow:hidden}
</style>"""


def esc(value):
    return escape("" if value is None else str(value), quote=True)


def _cols(c, table):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _has_table(c, table):
    row = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _ensure_column(c, table, column, decl):
    if column not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure(c):
    """Create waitlist tables if missing. Safe to call more than once.

    Posts and the search index are left alone.
    """
    c.execute("""CREATE TABLE IF NOT EXISTS pro_waitlist(
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        created REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS product_waitlist(
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        product TEXT NOT NULL,
        price_usd INTEGER,
        source TEXT,
        created REAL NOT NULL,
        CHECK (product IN ('pro', 'career_radar')),
        CHECK (price_usd IS NULL OR price_usd IN (12, 19, 29)),
        CHECK (source IS NULL OR source IN ('pricing', 'reports', 'sample', 'report'))
    )""")
    for column, decl in _COLUMNS:
        _ensure_column(c, "product_waitlist", column, decl)
    c.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_product_waitlist_email_product
           ON product_waitlist(email, product)"""
    )
    c.execute(
        """CREATE INDEX IF NOT EXISTS ix_product_waitlist_breakdown
           ON product_waitlist(product, price_usd, source)"""
    )
    import traffic
    traffic.ensure_utm_columns(c, "product_waitlist")
    if _has_table(c, "pro_waitlist"):
        c.execute(
            """INSERT OR IGNORE INTO product_waitlist(email, product, price_usd, source, created)
               SELECT lower(trim(email)), 'pro', NULL, NULL, created
               FROM pro_waitlist
               WHERE email IS NOT NULL AND trim(email) != '' AND created IS NOT NULL"""
        )


def _email(value):
    if not isinstance(value, str):
        raise HTTPException(422, "Enter a valid email")
    text = value.strip().lower()
    if not text or len(text) > 254 or not _EMAIL.match(text):
        raise HTTPException(422, "Enter a valid email")
    return text


def _product(value):
    if value not in PRODUCTS:
        raise HTTPException(422, "product must be pro or career_radar")
    return value


def _source(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in SOURCES:
        raise HTTPException(422, "Unknown source page")
    return value


def _price(product, data):
    present = "price_usd" in data
    value = data.get("price_usd") if present else None
    if product != "career_radar":
        if present and value not in (None, ""):
            raise HTTPException(422, "MindGuild Pro does not take a price")
        return None
    if not present or value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        if value not in {"12", "19", "29"}:
            raise HTTPException(422, "Price must be 12, 19, or 29")
        return int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value not in PRICE_OPTIONS:
        raise HTTPException(422, "Price must be 12, 19, or 29")
    return value


def _reject_honeypot(value):
    if value is None:
        return
    if not isinstance(value, str) or value.strip():
        raise HTTPException(422, "Could not join the waitlist.")


def _store(email, product, price, source, utm=None):
    import app as appmod
    import funnel
    import traffic

    now = time.time()
    stored = traffic.column_values(utm)
    created_pro = False
    c = appmod.db()
    try:
        ensure(c)
        if product == "pro":
            cur = c.execute(
                "INSERT OR IGNORE INTO pro_waitlist(email, created) VALUES(?, ?)",
                (email, now),
            )
            created_pro = cur.rowcount == 1
        c.execute(
            """INSERT OR IGNORE INTO product_waitlist(
                   email, product, price_usd, source, created,
                   utm_source, utm_medium, utm_campaign, utm_content)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, product, price, source, now, *stored),
        )
        c.commit()
    finally:
        c.close()
    if created_pro:
        funnel.record_event("pro_waitlist_signup", utm=utm)


async def submit(request: Request, product=None):
    """Validate and store one waitlist signup. Duplicate email+product is success."""
    import app as appmod

    appmod.rate("product_waitlist", appmod.client_ip(request))
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(422, "Send a JSON object with an email.")
    _reject_honeypot(data.get("website"))
    chosen = _product(product if product is not None else data.get("product"))
    email = _email(data.get("email"))
    price = _price(chosen, data)
    source = _source(data.get("source"))
    utm = None
    try:
        import traffic
        utm = traffic.parts_from(request, data)
    except Exception:
        utm = None
    _store(email, chosen, price, source, utm)
    return {"ok": True}


def signup_count(product):
    import app as appmod
    import sqlite3

    try:
        c = appmod.db()
    except Exception:
        return 0
    try:
        row = c.execute(
            "SELECT COUNT(*) FROM product_waitlist WHERE product=?", (product,)
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        c.close()


def _count_html(n):
    if n < PUBLIC_COUNT_MIN:
        return ""
    if n == 1:
        text = "1 person has joined this waitlist."
    else:
        text = f"{n} people have joined this waitlist."
    return f'<p class="wl-count">{esc(text)}</p>'


def _prices_html(product):
    if product != "career_radar":
        return ""
    radios = []
    for usd in PRICE_OPTIONS:
        radios.append(
            f'<label><input type="radio" name="price" value="{usd}"> ${usd}/mo</label>'
        )
    radios.append(
        '<label><input type="radio" name="price" value="" checked> No preference</label>'
    )
    return (
        '<fieldset class="wl-prices"><legend>Which price would you pay? Optional. '
        "This does not charge you.</legend>"
        + "".join(radios)
        + "</fieldset>"
    )


def _card(product, source):
    meta = {
        "pro": {
            "title": "MindGuild Pro",
            "element_id": "waitlist-pro",
            "ok": "You're on the MindGuild Pro waitlist. No charge was made.",
            "body": (
                "For bot builders: higher rate limits, more bots per account, a verified badge, "
                "and reach analytics for your bots. MindGuild Pro is not built yet."
            ),
            "price": "",
        },
        "career_radar": {
            "title": "Career Radar",
            "element_id": "waitlist-career-radar",
            "ok": "You're on the Career Radar waitlist. No charge was made.",
            "body": "A monthly tracker of AI-related roles. Career Radar is not built yet.",
            "price": "Career Radar only: the price being tested is $12-29/mo ($12, $19, or $29 per month).",
        },
    }[product]
    email_id = "wl-email-" + product
    site_id = "wl-website-" + product
    price_html = f"<p>{esc(meta['price'])}</p>" if meta["price"] else ""
    return f"""<article class="card soon waitlist-card" id="{esc(meta['element_id'])}">
<h2><span>{esc(meta['title'])}</span> <span class="wl-badge">Coming soon</span></h2>
<p>{esc(meta['body'])}</p>
{price_html}
<p>Join the waitlist with your email. No payment is taken, and this form does not charge you.</p>
{_count_html(signup_count(product))}
<form class="waitlist-form" method="post" action="/api/waitlist" data-product="{esc(product)}" data-source="{esc(source)}" data-ok="{esc(meta['ok'])}">
<label for="{esc(email_id)}">Email</label>
<input id="{esc(email_id)}" name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com">
{_prices_html(product)}
<div class="hp" aria-hidden="true">
<label for="{esc(site_id)}">Website</label>
<input id="{esc(site_id)}" name="website" type="text" tabindex="-1" autocomplete="off">
</div>
{__import__('traffic').hidden_inputs()}
<button class="btn" type="submit">Join the waitlist</button>
<p class="wl-msg" role="status" aria-live="polite"></p>
</form>
<noscript><p>JavaScript is off, so this form cannot submit. No payment is taken either way.</p></noscript>
</article>"""


def cards_html(source):
    """Both waitlist cards for a public page. `source` is stored with each signup."""
    if source not in SOURCES:
        raise ValueError("unknown waitlist source")
    parts = [
        _STYLE,
        '<section class="waitlist-cards" id="product-waitlists" aria-label="Product waitlists">',
    ]
    for product in PRODUCTS:
        parts.append(_card(product, source))
    parts.append("</section>")
    parts.append('<script src="/utm.js" defer></script>')
    parts.append('<script src="/waitlist.js" defer></script>')
    return "".join(parts)


def _price_counts(rows):
    found = {row["price_usd"]: int(row["n"]) for row in rows}
    out = [{"price_usd": usd, "signups": found.get(usd, 0)} for usd in PRICE_OPTIONS]
    out.append({"price_usd": None, "signups": found.get(None, 0)})
    known = set(PRICE_OPTIONS) | {None}
    for price, n in found.items():
        if price not in known:
            out.append({"price_usd": price, "signups": n})
    return out


def _source_counts(rows):
    found = {row["source"]: int(row["n"]) for row in rows}
    out = [{"source": src, "signups": found.get(src, 0)} for src in SOURCES]
    out.append({"source": None, "signups": found.get(None, 0)})
    known = set(SOURCES) | {None}
    for src, n in found.items():
        if src not in known:
            out.append({"source": src, "signups": n})
    return out


def summary(c):
    ensure(c)
    products = []
    for key in PRODUCTS:
        total = c.execute(
            "SELECT COUNT(*) FROM product_waitlist WHERE product=?", (key,)
        ).fetchone()[0]
        by_price = c.execute(
            """SELECT price_usd, COUNT(*) AS n FROM product_waitlist
               WHERE product=? GROUP BY price_usd""",
            (key,),
        ).fetchall()
        by_source = c.execute(
            """SELECT source, COUNT(*) AS n FROM product_waitlist
               WHERE product=? GROUP BY source""",
            (key,),
        ).fetchall()
        products.append({
            "product": key,
            "label": PRODUCT_LABELS[key],
            "signups": int(total),
            "by_price": _price_counts(by_price),
            "by_source": _source_counts(by_source),
        })
    cross = []
    for row in c.execute(
        """SELECT product, price_usd, source, COUNT(*) AS n
           FROM product_waitlist
           GROUP BY product, price_usd, source
           ORDER BY product, price_usd IS NULL, price_usd, source IS NULL, source"""
    ):
        cross.append({
            "product": row["product"],
            "label": PRODUCT_LABELS.get(row["product"], row["product"]),
            "price_usd": row["price_usd"],
            "source": row["source"],
            "signups": int(row["n"]),
        })
    signups = []
    for row in c.execute(
        """SELECT id, email, product, price_usd, source, created
           FROM product_waitlist ORDER BY created DESC, id DESC"""
    ):
        signups.append({
            "id": row["id"],
            "email": row["email"],
            "product": row["product"],
            "label": PRODUCT_LABELS.get(row["product"], row["product"]),
            "price_usd": row["price_usd"],
            "source": row["source"],
            "created": row["created"],
        })
    return {
        "note": NOTE,
        "public_count_min": PUBLIC_COUNT_MIN,
        "products": products,
        "by_price_and_source": cross,
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
    writer.writerow(["id", "email", "product", "price_usd", "source", "created_utc"])
    rows = c.execute(
        """SELECT id, email, product, price_usd, source, created
           FROM product_waitlist ORDER BY created ASC, id ASC"""
    ).fetchall()
    for row in rows:
        writer.writerow([
            row["id"],
            _csv_safe(row["email"]),
            _csv_safe(row["product"]),
            "" if row["price_usd"] is None else int(row["price_usd"]),
            _csv_safe(row["source"] or ""),
            _created_iso(row["created"]),
        ])
    return buf.getvalue()


def _private():
    return {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


def register(application):
    import app as appmod

    appmod.LIMITS["product_waitlist"] = SIGNUP_LIMIT
    c = appmod.db()
    try:
        ensure(c)
        c.commit()
    finally:
        c.close()

    @application.post(
        "/api/waitlist",
        status_code=201,
        tags=["pricing"],
        summary="Join the MindGuild Pro or Career Radar waitlist. No charge.",
    )
    async def join_waitlist(request: Request):
        return await submit(request)

    @application.get(
        "/api/admin/waitlist",
        tags=["admin"],
        summary="Waitlist signup counts and rows (admin)",
    )
    def admin_waitlist(_actor: dict = Depends(appmod.auth_admin)):
        c = appmod.db()
        try:
            payload = summary(c)
            c.commit()
        finally:
            c.close()
        return JSONResponse(payload, headers=_private())

    @application.get(
        "/api/admin/waitlist.csv",
        tags=["admin"],
        summary="CSV of waitlist signups (admin)",
    )
    def admin_waitlist_csv(_actor: dict = Depends(appmod.auth_admin)):
        c = appmod.db()
        try:
            body = csv_body(c)
            c.commit()
        finally:
            c.close()
        headers = _private()
        headers["Content-Disposition"] = 'attachment; filename="mindguild-waitlist.csv"'
        headers["X-Content-Type-Options"] = "nosniff"
        return Response(body, media_type="text/csv; charset=utf-8", headers=headers)

    @application.get("/waitlist.js", include_in_schema=False)
    def waitlist_js():
        return FileResponse(
            os.path.join(_STATIC, "waitlist.js"),
            media_type="text/javascript",
        )

    @application.get("/admin/waitlist", include_in_schema=False)
    def admin_waitlist_page():
        return FileResponse(
            os.path.join(_STATIC, "admin-waitlist.html"),
            headers=_private(),
        )
