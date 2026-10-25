"""MindGuild /pricing page, ad Payment Links, and the MindGuild Pro waitlist.

Ad tiers are sold through Stripe Payment Links. The URLs come from env vars
(AD_LINK_DAY, AD_LINK_WEEK, AD_LINK_SECTION,
FEATURED_LINK_WEEK, FEATURED_LINK_MONTH) so live links can replace the
test-mode defaults without a code change. No Stripe API calls happen here.

The Pro waitlist stores emails in SQLite (pro_waitlist) and records a
pro_waitlist_signup funnel event. The same signup is also stored in
product_waitlist, which Career Radar uses, so /admin/waitlist can count
both. No Stripe call and no email is sent from either waitlist. The
database file is on the persistent Render disk at /data and survives redeploy.
"""

import os
import re

from fastapi import Request
from fastapi.responses import HTMLResponse

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

    # The enforced bucket is product_waitlist in waitlist.py (5/hour, shared
    # with Career Radar). This name stays so the old limit key is still defined.
    appmod.LIMITS["pro_waitlist"] = (5, 3600)
    c = appmod.db()
    try:
        ensure_table(c)
        c.commit()
    finally:
        c.close()

    @application.get("/pricing", include_in_schema=False)
    def pricing_page():
        import waitlist

        with open(_PAGE, encoding="utf-8") as handle:
            html = handle.read()
        marker = "<!-- product-waitlist -->"
        if marker not in html:
            raise RuntimeError("pricing page is missing the waitlist marker")
        html = html.replace(marker, waitlist.cards_html("pricing"), 1)
        return HTMLResponse(html)

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
            "career_radar": {
                "status": "not_built",
                "price": "$12-29/mo",
                "price_options_usd": [12, 19, 29],
                "waitlist": "/api/waitlist",
                "note": "Waitlist only. No payment is taken.",
            },
        }

    @application.post("/api/pro/waitlist", status_code=201, tags=["pricing"], summary="Join the MindGuild Pro waitlist")
    async def pro_waitlist(request: Request):
        import waitlist

        return await waitlist.submit(request, product="pro")
