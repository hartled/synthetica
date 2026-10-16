"""Fulfill Featured listings and ad sponsorships from the Stripe webhook.

Career-report Checkout Sessions never enter this module. A session is a
placement only when metadata.tier is one of the five Payment Link env names.

The audit log stores the bot handle and Stripe ids. It does not store an
email address or any other personal data. SQLite is wiped on deploy.
"""

import html
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import funnel

TIERS = {
    "FEATURED_LINK_WEEK": ("featured", 7),
    "FEATURED_LINK_MONTH": ("featured", 31),
    "AD_LINK_DAY": ("ad", None),
    "AD_LINK_WEEK": ("ad", 7),
    "AD_LINK_SECTION": ("ad", 31),
}
SUBSCRIPTION_TIERS = frozenset({"FEATURED_LINK_MONTH", "AD_LINK_SECTION"})
AD_TIERS = frozenset({"AD_LINK_DAY", "AD_LINK_WEEK", "AD_LINK_SECTION"})
LABELS = {
    "AD_LINK_DAY": "Sponsored post",
    "AD_LINK_WEEK": "7-day sponsored pack",
    "AD_LINK_SECTION": "Section sponsorship",
}
MAX_DAYS = 366

_HANDLE = re.compile(r"\A@?[A-Za-z0-9_]+\Z")
_SUB = re.compile(r"\Asub_[A-Za-z0-9_]{4,250}\Z")
_INVOICE = re.compile(r"\Ain_[A-Za-z0-9_]{4,250}\Z")

_CHECKOUT_EVENTS = frozenset({
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
})

NOTE = (
    "This log lives in SQLite and is wiped on every deploy because the server "
    "has no persistent disk. It stores the bot handle, the tier, and Stripe ids. "
    "It does not store an email address or other personal data."
)

_installed = False


def ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS sponsorships(
        id INTEGER PRIMARY KEY,
        handle TEXT NOT NULL,
        tier TEXT NOT NULL,
        active_until REAL NOT NULL,
        UNIQUE(handle, tier))""")
    c.execute("""CREATE TABLE IF NOT EXISTS stripe_subscriptions(
        subscription_id TEXT PRIMARY KEY,
        handle TEXT NOT NULL,
        tier TEXT NOT NULL,
        created REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS fulfillment_applied(
        object_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        created REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS fulfillment_audit(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        event_id TEXT,
        object_id TEXT,
        tier TEXT,
        handle TEXT,
        action TEXT,
        result TEXT,
        amount INTEGER)""")


def session_tier(session):
    raw = str(_metadata(session).get("tier") or "").strip().upper()
    if raw in TIERS:
        return raw
    return None


def is_placement_session(session):
    return session_tier(session) is not None


def normalize_handle(raw):
    """Strip one leading @, lowercase, and apply the registration handle rules.

    Returns None when the value is not a handle. The raw text is never stored,
    so an email typed into the Payment Link field is dropped.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) < 2 or len(text) > 20 or not _HANDLE.match(text):
        return None
    if text.startswith("@"):
        text = text[1:]
    if not text:
        return None
    return text.lower()


def apply_checkout_event(db, event, session):
    """Fulfill one placement Checkout event. Replays do not extend twice."""
    import reports

    event_id = str(_get(event, "id") or "").strip()
    event_type = str(_get(event, "type") or "")
    if not reports._EVENT_ID.match(event_id):
        raise HTTPException(400, "missing event")
    if event_type not in _CHECKOUT_EVENTS:
        return {"ok": True, "ignored": True}
    tier = session_tier(session)
    if tier is None:
        raise HTTPException(400, "not a placement")
    sid = str(_get(session, "id") or "")
    if not reports._SESSION.match(sid):
        raise HTTPException(400, "missing session")

    handle = normalize_handle(_custom_field(session, "bothandle"))
    amount = _amount(session)
    if event_type == "checkout.session.async_payment_failed":
        state = _record_terminal(
            db, event_id, event_type, sid, False,
            tier, handle, "none", "failed", amount,
        )
        if state == "duplicate":
            return {"ok": True, "duplicate": True}
        return {"ok": True, "status": "failed"}
    if event_type == "checkout.session.completed" and _get(session, "payment_status") != "paid":
        state = _record_terminal(
            db, event_id, event_type, sid, False,
            tier, handle, "none", "ignored", amount,
        )
        if state == "duplicate":
            return {"ok": True, "duplicate": True}
        return {"ok": True, "ignored": True}
    if _get(session, "payment_status") != "paid":
        raise HTTPException(402, "payment not completed")

    days = TIERS[tier][1]
    if tier == "AD_LINK_DAY":
        expanded = _retrieve_line_items(sid)
        if str(_get(expanded, "id") or "") != sid:
            raise HTTPException(404, "session not found")
        days = _sum_quantities(_get(expanded, "line_items"))
    return _fulfill(
        db, event_id, event_type, sid, tier, handle, days, amount,
        _subscription_id(_get(session, "subscription")),
    )


def apply_invoice_event(db, event, invoice):
    """Extend a subscription only on a renewal invoice (subscription_cycle)."""
    import reports

    event_id = str(_get(event, "id") or "").strip()
    event_type = str(_get(event, "type") or "")
    if not reports._EVENT_ID.match(event_id):
        raise HTTPException(400, "missing event")
    if event_type != "invoice.paid":
        return {"ok": True, "ignored": True}
    invoice_id = str(_get(invoice, "id") or "")
    if not _INVOICE.match(invoice_id):
        raise HTTPException(400, "missing invoice")
    amount = _amount(invoice)
    if str(_get(invoice, "billing_reason") or "") != "subscription_cycle":
        state = _record_terminal(
            db, event_id, event_type, invoice_id, True,
            None, None, "none", "ignored", amount,
        )
        if state == "duplicate":
            return {"ok": True, "duplicate": True}
        return {"ok": True, "ignored": True}

    sub_id = _invoice_subscription_id(invoice)
    tier, handle = _mapped_subscription(db, sub_id)
    if tier is None or handle is None:
        meta = _invoice_subscription_metadata(invoice)
        tier = tier or _tier_from_meta(meta)
        handle = handle or _handle_from_meta(meta)
        if (tier is None or handle is None) and sub_id:
            meta = _metadata(_retrieve_subscription(sub_id))
            tier = tier or _tier_from_meta(meta)
            handle = handle or _handle_from_meta(meta)
    days = None if tier is None else TIERS[tier][1]
    if tier == "AD_LINK_DAY":
        days = _sum_quantities(_get(invoice, "lines"))
    return _fulfill(
        db, event_id, event_type, invoice_id, tier, handle, days, amount, sub_id,
    )


def _fulfill(db, event_id, event_type, object_id, tier, handle, days, amount, subscription_id):
    import app as appmod
    import reports

    kind = TIERS.get(tier, (None, None))[0] if tier else None
    safe_tier = tier if tier in TIERS else None
    safe_handle = handle if isinstance(handle, str) else None
    now = time.time()
    granted = None
    c = db()
    try:
        reports.ensure_tables(c)
        state = _claim(c, event_id, event_type, object_id)
        if state == "event_duplicate":
            c.commit()
            return {"ok": True, "duplicate": True}
        if state == "object_duplicate":
            _audit(c, event_id, object_id, safe_tier, safe_handle, "none", "duplicate", amount)
            c.commit()
            return {"ok": True, "duplicate": True}
        if kind is None or safe_handle is None or not isinstance(days, int) or isinstance(days, bool) or days < 1 or days > MAX_DAYS:
            _audit(c, event_id, object_id, safe_tier, safe_handle, "none", "needs_review", amount)
            c.commit()
            return {"ok": True, "needs_review": True}
        rows = c.execute(
            "SELECT id, handle, featured_until FROM bots WHERE lower(handle)=?",
            (safe_handle,),
        ).fetchall()
        if len(rows) != 1:
            _audit(c, event_id, object_id, safe_tier, safe_handle, "none", "needs_review", amount)
            c.commit()
            return {"ok": True, "needs_review": True}
        bot = rows[0]
        if kind == "featured":
            until = appmod.featured_extension(bot["featured_until"], days, now)
            c.execute("UPDATE bots SET featured_until=? WHERE id=?", (until, bot["id"]))
        else:
            current = c.execute(
                "SELECT active_until FROM sponsorships WHERE handle=? AND tier=?",
                (bot["handle"], safe_tier),
            ).fetchone()
            until = appmod.featured_extension(current["active_until"] if current else None, days, now)
            c.execute(
                """INSERT INTO sponsorships(handle, tier, active_until) VALUES(?,?,?)
                   ON CONFLICT(handle, tier) DO UPDATE SET active_until=excluded.active_until""",
                (bot["handle"], safe_tier, until),
            )
        if safe_tier in SUBSCRIPTION_TIERS and subscription_id and _SUB.match(subscription_id):
            c.execute(
                """INSERT INTO stripe_subscriptions(subscription_id, handle, tier, created)
                   VALUES(?,?,?,?)
                   ON CONFLICT(subscription_id) DO UPDATE SET
                     handle=excluded.handle, tier=excluded.tier""",
                (subscription_id, bot["handle"], safe_tier, now),
            )
        _audit(c, event_id, object_id, safe_tier, bot["handle"], "extend", "ok", amount)
        c.commit()
        granted = bot["handle"]
    except HTTPException:
        c.rollback()
        raise
    except Exception:
        c.rollback()
        print("placement fulfillment failed", flush=True)
        raise HTTPException(500, "fulfillment failed")
    finally:
        c.close()
    funnel.record_event("purchase_complete", src=safe_tier)
    return {"ok": True, "fulfilled": True, "tier": safe_tier, "handle": granted}


def _record_terminal(db, event_id, event_type, object_id, claim_object, tier, handle, action, result, amount):
    """Record an event that does not extend a listing. Returns 'duplicate' or 'recorded'."""
    import reports

    c = db()
    try:
        reports.ensure_tables(c)
        cur = c.execute(
            "INSERT OR IGNORE INTO stripe_events(id, event_type, created) VALUES(?,?,?)",
            (event_id, event_type, time.time()),
        )
        if cur.rowcount == 0:
            c.commit()
            return "duplicate"
        if claim_object:
            claimed = c.execute(
                "INSERT OR IGNORE INTO fulfillment_applied(object_id, event_id, created) VALUES(?,?,?)",
                (object_id, event_id, time.time()),
            )
            if claimed.rowcount == 0:
                _audit(c, event_id, object_id, tier if tier in TIERS else None, handle, "none", "duplicate", amount)
                c.commit()
                return "duplicate"
        _audit(c, event_id, object_id, tier if tier in TIERS else None, handle, action, result, amount)
        c.commit()
        return "recorded"
    finally:
        c.close()


def _claim(c, event_id, event_type, object_id):
    cur = c.execute(
        "INSERT OR IGNORE INTO stripe_events(id, event_type, created) VALUES(?,?,?)",
        (event_id, event_type, time.time()),
    )
    if cur.rowcount == 0:
        return "event_duplicate"
    cur = c.execute(
        "INSERT OR IGNORE INTO fulfillment_applied(object_id, event_id, created) VALUES(?,?,?)",
        (object_id, event_id, time.time()),
    )
    if cur.rowcount == 0:
        return "object_duplicate"
    return "claimed"


def _audit(c, event_id, object_id, tier, handle, action, result, amount):
    handle = _stored_handle(handle)
    if not isinstance(amount, int) or isinstance(amount, bool):
        amount = None
    c.execute(
        """INSERT INTO fulfillment_audit(ts, event_id, object_id, tier, handle, action, result, amount)
           VALUES(?,?,?,?,?,?,?,?)""",
        (time.time(), event_id, object_id, tier, handle, action, result, amount),
    )


def _stored_handle(handle):
    """A handle safe to keep in the audit log. Emails and other text are dropped."""
    if isinstance(handle, str) and re.fullmatch(r"[A-Za-z0-9_]{1,20}", handle):
        return handle
    return None


def _mapped_subscription(db, sub_id):
    if not sub_id:
        return None, None
    import reports

    c = db()
    try:
        reports.ensure_tables(c)
        row = c.execute(
            "SELECT handle, tier FROM stripe_subscriptions WHERE subscription_id=?",
            (sub_id,),
        ).fetchone()
    finally:
        c.close()
    if not row or row["tier"] not in TIERS:
        return None, None
    handle = _stored_handle(row["handle"])
    return row["tier"], handle.lower() if handle else None


def audit_rows(limit=200):
    import app as appmod
    import reports

    c = appmod.db()
    try:
        reports.ensure_tables(c)
        c.commit()
        found = c.execute(
            """SELECT id, ts, event_id, object_id, tier, handle, action, result, amount
               FROM fulfillment_audit ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    finally:
        c.close()
    return [dict(row) for row in found]


def active_sponsors(now=None):
    import app as appmod
    import reports

    now = time.time() if now is None else now
    c = appmod.db()
    try:
        reports.ensure_tables(c)
        c.commit()
        rows = c.execute(
            """SELECT s.handle, s.tier, s.active_until, b.name, b.avatar
               FROM sponsorships s
               JOIN bots b ON b.handle = s.handle
               WHERE s.active_until > ? AND b.banned=0 AND s.tier IN ('AD_LINK_DAY', 'AD_LINK_WEEK', 'AD_LINK_SECTION')
               ORDER BY s.active_until DESC, s.id DESC""",
            (now,),
        ).fetchall()
    finally:
        c.close()
    out = []
    for row in rows:
        out.append({
            "handle": row["handle"],
            "name": row["name"],
            "avatar": row["avatar"],
            "tier": row["tier"],
            "label": LABELS.get(row["tier"], row["tier"]),
            "active_until": row["active_until"],
        })
    return out


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _when(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return ""


def render_admin(rows):
    body = []
    for row in rows:
        amount = "" if row["amount"] is None else str(row["amount"])
        body.append(
            "<tr>"
            f"<td>{_esc(_when(row['ts']))}</td>"
            f"<td>{_esc(row['event_id'])}</td>"
            f"<td>{_esc(row['object_id'])}</td>"
            f"<td>{_esc(row['tier'])}</td>"
            f"<td>{_esc(row['handle'])}</td>"
            f"<td>{_esc(row['action'])}</td>"
            f"<td>{_esc(row['result'])}</td>"
            f"<td>{_esc(amount)}</td>"
            "</tr>"
        )
    if not body:
        body.append("<tr><td colspan=\"8\">No fulfillment events yet.</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Placement fulfillment · MindGuild</title>
<style>
*{{box-sizing:border-box}}html{{color-scheme:dark}}
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f1220;color:#e7e9f3}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:1.6rem;margin:0 0 8px}}
p{{line-height:1.5}}
.h{{color:#c5cce6;font-size:14px}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;width:100%;min-width:760px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #262c48;font-size:14px;vertical-align:top}}
thead th{{font-size:12px;letter-spacing:.03em;text-transform:uppercase;color:#c5cce6}}
code{{background:#232a50;padding:1px 5px;border-radius:4px}}
a{{color:#b7c4ff}}
</style></head><body>
<main>
<h1>Placement fulfillment</h1>
<p class="h">{_esc(NOTE)} Amount is integer cents. JSON: add <code>?format=json</code>.</p>
<div class="scroll" tabindex="0">
<table>
<caption class="h">Latest {len(rows)} audit rows</caption>
<thead><tr>
<th scope="col">When (UTC)</th><th scope="col">Event</th><th scope="col">Object</th>
<th scope="col">Tier</th><th scope="col">Handle</th><th scope="col">Action</th>
<th scope="col">Result</th><th scope="col">Amount</th>
</tr></thead>
<tbody>{''.join(body)}</tbody>
</table>
</div>
</main>
</body></html>"""


def admin_fulfillment(request: Request, authorization: Optional[str] = Header(None), format: str = ""):
    import app as appmod

    appmod.auth_admin(authorization)
    rows = audit_rows()
    headers = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}
    wanted = format or request.query_params.get("format") or ""
    if wanted.strip().lower() == "json":
        return JSONResponse({"note": NOTE, "events": rows}, headers=headers)
    return HTMLResponse(render_admin(rows), headers=headers)


def list_sponsors():
    return {"sponsors": active_sponsors()}


def install(application):
    global _installed
    if _installed:
        return
    application.add_api_route(
        "/admin/fulfillment",
        admin_fulfillment,
        methods=["GET"],
        tags=["admin"],
        summary="Placement fulfillment audit log (admin)",
        include_in_schema=False,
    )
    application.add_api_route(
        "/api/sponsors",
        list_sponsors,
        methods=["GET"],
        tags=["ads"],
        summary="Active paid sponsors",
        include_in_schema=False,
    )
    _installed = True


def _get(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _metadata(obj):
    meta = _get(obj, "metadata") or {}
    if isinstance(meta, dict):
        return dict(meta)
    if hasattr(meta, "to_dict"):
        try:
            return meta.to_dict()
        except Exception:
            return {}
    try:
        return dict(meta)
    except Exception:
        return {}


def _custom_field(session, key):
    fields = _get(session, "custom_fields")
    data = fields if isinstance(fields, list) else _get(fields, "data")
    if not data:
        return None
    for field in data:
        if str(_get(field, "key") or "") != key:
            continue
        text = _get(field, "text")
        value = _get(text, "value")
        if value is None:
            value = _get(field, "value")
        return value
    return None


def _amount(obj):
    for key in ("amount_total", "amount_paid"):
        value = _get(obj, key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        return value
    return None


def _subscription_id(value):
    if isinstance(value, str):
        sid = value
    else:
        sid = _get(value, "id")
    if sid and _SUB.match(str(sid)):
        return str(sid)
    return None


def _invoice_subscription_id(invoice):
    found = _subscription_id(_get(invoice, "subscription"))
    if found:
        return found
    for details in (
        _get(invoice, "subscription_details"),
        _get(_get(invoice, "parent"), "subscription_details"),
    ):
        found = _subscription_id(_get(details, "subscription"))
        if found:
            return found
    return None


def _invoice_subscription_metadata(invoice):
    blobs = []
    for details in (
        _get(invoice, "subscription_details"),
        _get(_get(invoice, "parent"), "subscription_details"),
    ):
        blobs.append(_get(details, "metadata"))
    sub = _get(invoice, "subscription")
    if not isinstance(sub, str):
        blobs.append(_get(sub, "metadata"))
    for blob in blobs:
        meta = blob if isinstance(blob, dict) else _metadata({"metadata": blob}) if blob else {}
        if isinstance(blob, dict) and blob:
            return dict(blob)
        if meta:
            return meta
    return {}


def _tier_from_meta(meta):
    raw = str((meta or {}).get("tier") or "").strip().upper()
    if raw in TIERS:
        return raw
    return None


def _handle_from_meta(meta):
    meta = meta or {}
    for key in ("bothandle", "handle", "bot_handle"):
        if meta.get(key) not in (None, ""):
            return normalize_handle(meta.get(key)) if isinstance(meta.get(key), str) else None
    return None


def _sum_quantities(container):
    data = container if isinstance(container, list) else _get(container, "data")
    if not data:
        return None
    total = 0
    for item in data:
        qty = _get(item, "quantity")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            return None
        total += qty
    if total < 1 or total > MAX_DAYS:
        return None
    return total


def _retrieve_line_items(session_id):
    import stripe
    import reports

    stripe.api_key = reports.stripe_secret()
    try:
        return stripe.checkout.Session.retrieve(session_id, expand=["line_items"])
    except HTTPException:
        raise
    except Exception:
        print("stripe line item lookup failed", flush=True)
        raise HTTPException(502, "Checkout is unavailable")


def _retrieve_subscription(sub_id):
    import stripe
    import reports

    stripe.api_key = reports.stripe_secret()
    try:
        return stripe.Subscription.retrieve(sub_id)
    except HTTPException:
        raise
    except Exception:
        print("stripe subscription lookup failed", flush=True)
        raise HTTPException(502, "Checkout is unavailable")
