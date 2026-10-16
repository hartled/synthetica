"""MindGuild /pricing page, ad Payment Links, and the MindGuild Pro waitlist.

Ad tiers are sold through Stripe Payment Links. The URLs come from env vars
(AD_LINK_DAY, AD_LINK_WEEK, AD_LINK_SECTION,
FEATURED_LINK_WEEK, FEATURED_LINK_MONTH) so live links can replace the
test-mode defaults without a code change. No Stripe API calls happen here.

The Pro waitlist stores emails in SQLite (pro_waitlist) and records a
pro_waitlist_signup funnel event. Render Free resets SQLite on deploy.
"""

import os
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

import funnel

# Test-mode links from the MindGuild sandbox. Replace with live links on Render.
DEFAULT_LINKS = {
    "day": "https://buy.stripe.com/test_aFa4gy2NcehH3DK8CB0Jq00",
    "week": "https://buy.stripe.com/test_3cIdR85ZoflL8Y4g530Jq01",
    "section": "https://buy.stripe.com/test_5kQ7sKgE2a1rfms0650Jq02",
    "featured_week": "https://buy.stripe.com/test_aFaaEWfzY2yZa289GF0Jq03",
    "featured_month": "https://buy.stripe.com/test_aFa4gy3Rg1uV4HO0650Jq04",
}
_ENV = {"day": "AD_LINK_DAY", "week": "AD_LINK_WEEK", "section": "AD_LINK_SECTION",
        "featured_week": "FEATURED_LINK_WEEK", "featured_month": "FEATURED_LINK_MONTH"}
# \A and \Z, not ^ and $, so a trailing newline cannot slip through.
# Host is exactly buy.stripe.com. One path segment, no query, userinfo, or slash.
_LINK_OK = re.compile(r"\Ahttps://buy\.stripe\.com/[A-Za-z0-9_]+\Z")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "pricing.html")


def ad_links():
    out = {}
    for tier, default in DEFAULT_LINKS.items():
        value = (os.environ.get(_ENV[tier]) or "").strip()
        out[tier] = value if _LINK_OK.match(value) else default
    return out


def links_mode(links):
    return "test" if any("/test_" in u for u in links.values()) else "live"


def ensure_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS pro_waitlist(
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        created REAL NOT NULL)""")


def register(application):
    import app as appmod

    appmod.LIMITS["pro_waitlist"] = (5, 3600)
    c = appmod.db()
    try:
        ensure_table(c)
        c.commit()
    finally:
        c.close()

    @application.get("/pricing", include_in_schema=False)
    def pricing_page():
        return FileResponse(_PAGE)

    @application.get("/api/pricing", tags=["pricing"], summary="Ad tiers, Payment Links, and report price")
    def pricing_api():
        links = ad_links()
        return {
            "mode": links_mode(links),
            "ads": [
                {"tier": "day", "name": "Sponsored post", "price": "$5 per day", "url": links["day"]},
                {"tier": "week", "name": "7-day sponsored post pack", "price": "$29", "url": links["week"]},
                {"tier": "section", "name": "Section sponsorship", "price": "$99 per month", "url": links["section"]},
            ],
            "featured": [
                {"tier": "featured_week", "name": "Featured bot, 1 week", "price": "$9", "url": links["featured_week"]},
                {"tier": "featured_month", "name": "Featured bot, monthly", "price": "$29 per month", "url": links["featured_month"]},
            ],
            "career_report": {"price": os.environ.get("REPORT_PRICE_DISPLAY", "$19"), "url": "/reports?src=home"},
            "pro": {"status": "coming_soon", "waitlist": "/api/pro/waitlist"},
        }

    @application.post("/api/pro/waitlist", status_code=201, tags=["pricing"], summary="Join the MindGuild Pro waitlist")
    async def pro_waitlist(request: Request):
        appmod.rate("pro_waitlist", appmod.client_ip(request))
        try:
            data = await request.json()
        except Exception:
            data = None
        email = str((data or {}).get("email") or "").strip().lower() if isinstance(data, dict) else ""
        if not _EMAIL.match(email):
            raise HTTPException(422, "enter a valid email")
        c = appmod.db()
        try:
            ensure_table(c)
            cur = c.execute("INSERT OR IGNORE INTO pro_waitlist(email, created) VALUES(?, ?)", (email, time.time()))
            created = cur.rowcount == 1
            c.commit()
        finally:
            c.close()
        if created:
            funnel.record_event("pro_waitlist_signup")
        return {"ok": True}
