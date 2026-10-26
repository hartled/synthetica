"""First-party report funnel counts and the shared Career Report links.

No Stripe calls. Checkout is mocked. The funnel table stores no IP, cookie,
or user agent.
"""

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest import mock

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
for _key in (
    "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
    "WAITLIST_WEBHOOK_URL", "ADMIN_KEY",
):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import funnel

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR_D = {"Authorization": "Bearer " + KEY_D}
HDR_CEO = {"Authorization": "Bearer " + KEY_CEO}
INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _sign(secret, raw):
    stamp = str(int(time.time()))
    mac = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


class FunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(KEY_D, KEY_CEO)
        cls.client = TestClient(app.app)

    def setUp(self):
        app._hits.clear()
        for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(key, None)
        c = app.db()
        try:
            funnel.ensure_table(c)
            c.execute("DELETE FROM funnel_events")
            c.commit()
        finally:
            c.close()
        funnel.reset_user_agent(funnel.note_user_agent(""))

    def _rows(self, event=None):
        c = app.db()
        try:
            if event:
                found = c.execute(
                    "SELECT event, src, report_id FROM funnel_events WHERE event=? ORDER BY id",
                    (event,),
                ).fetchall()
            else:
                found = c.execute(
                    "SELECT event, src, report_id FROM funnel_events ORDER BY id"
                ).fetchall()
            return [dict(row) for row in found]
        finally:
            c.close()

    def _count(self, event, src=None, report_id=None):
        rows = self._rows(event)
        if src is not None:
            rows = [row for row in rows if row["src"] == src]
        if report_id is not None:
            rows = [row for row in rows if row["report_id"] == report_id]
        return len(rows)

    def test_table_shape_stores_no_personal_data(self):
        c = app.db()
        try:
            funnel.ensure_table(c)
            cols = [row[1] for row in c.execute("PRAGMA table_info(funnel_events)")]
            sql = c.execute(
                "SELECT sql FROM sqlite_master WHERE name='funnel_events'"
            ).fetchone()[0]
        finally:
            c.close()
        self.assertEqual(cols, [
            "id", "ts", "event", "src", "report_id",
            "utm_source", "utm_medium", "utm_campaign", "utm_content",
        ])
        self.assertNotIn("NOT NULL", sql.split("report_id")[1])
        blob = sql.lower()
        for banned in ("ip", "cookie", "user_agent", "email", "user-agent"):
            self.assertNotIn(banned, blob)

    def test_views_keep_src_and_skip_bots_and_junk(self):
        human = {"User-Agent": "Mozilla/5.0 (human-visitor) FunnelHuman/1.0"}
        for src in ("card", "lab", "home"):
            page = self.client.get("/reports?src=" + src, headers=human)
            self.assertEqual(page.status_code, 200, page.text)
        bare = self.client.get("/reports", headers=human)
        self.assertEqual(bare.status_code, 200)
        junk = self.client.get("/reports?src=person@example.com", headers=human)
        self.assertEqual(junk.status_code, 200)
        script = self.client.get("/reports?src=%3Cscript%3E", headers=human)
        self.assertEqual(script.status_code, 200)
        upper = self.client.get("/reports?src=CARD", headers=human)
        self.assertEqual(upper.status_code, 200)
        bot = self.client.get("/reports?src=home", headers={"User-Agent": "Mozilla/5.0 Googlebot/2.1"})
        self.assertEqual(bot.status_code, 200)
        curl = self.client.get("/reports?src=lab", headers={"User-Agent": "curl/8.4.0"})
        self.assertEqual(curl.status_code, 200)
        rows = self._rows("reports_view")
        self.assertEqual(
            [row["src"] for row in rows],
            ["card", "lab", "home", None, None, None, "card"],
        )
        stored = json.dumps(rows)
        self.assertNotIn("example.com", stored)
        self.assertNotIn("script", stored.lower())
        self.assertNotIn("FunnelHuman", stored)
        self.assertNotIn("Googlebot", stored)
        cancel = self.client.get("/reports?src=lab&checkout=cancel", headers=human)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(self._count("reports_view", src="lab"), 2)

    def test_direct_record_skips_bots_and_counts_purchase_once(self):
        report_id = "a" * 24
        other = "b" * 24
        token = funnel.note_user_agent("python-requests/2.32")
        try:
            self.assertFalse(funnel.record_event("reports_view", src="home"))
        finally:
            funnel.reset_user_agent(token)
        self.assertTrue(funnel.record_event("checkout_start", src="card", report_id=report_id))
        self.assertTrue(funnel.record_event("purchase_complete", report_id=report_id))
        self.assertFalse(funnel.record_event("purchase_complete", report_id=report_id))
        self.assertTrue(funnel.record_event("purchase_complete", src="home", report_id=other))
        self.assertFalse(funnel.record_event("purchase_complete", report_id="short"))
        self.assertFalse(funnel.record_event("not-an-event"))
        purchases = self._rows("purchase_complete")
        self.assertEqual(len(purchases), 2)
        copied = next(row for row in purchases if row["report_id"] == report_id)
        self.assertEqual(copied["src"], "card")
        self.assertEqual(self._count("reports_view"), 0)

    def test_checkout_start_and_single_purchase_across_both_fulfillment_paths(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        human = {"User-Agent": "Mozilla/5.0 (human-visitor) FunnelHuman/1.0"}

        class Session:
            def __init__(self):
                self.id = "cs_test_funnel123"
                self.url = "https://checkout.stripe.com/c/pay/cs_test_funnel123"
                self.payment_status = "unpaid"
                self.metadata = {}
                self.client_reference_id = ""
                self.customer_details = {"email": "Buyer@Example.com"}

        session = Session()

        def stripe_create(**kwargs):
            session.metadata = dict(kwargs.get("metadata") or {})
            session.client_reference_id = kwargs.get("client_reference_id")
            return session

        def retrieve(sid):
            self.assertEqual(sid, session.id)
            return session

        with mock.patch("stripe.checkout.Session.create", stripe_create), mock.patch(
            "stripe.checkout.Session.retrieve", retrieve
        ):
            bad = self.client.post("/api/reports/checkout", json={"current_job": ""}, headers=human)
            self.assertEqual(bad.status_code, 422)
            self.assertEqual(self._count("checkout_start"), 0)

            def explode(**kwargs):
                raise RuntimeError("stripe down")

            with mock.patch("stripe.checkout.Session.create", explode):
                down = self.client.post("/api/reports/checkout", json=INTAKE, headers=human)
                self.assertEqual(down.status_code, 502)
            self.assertEqual(self._count("checkout_start"), 0)

            started = self.client.post(
                "/api/reports/checkout?src=card",
                json=INTAKE,
                headers=human,
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(started.json()["checkout_url"], session.url)
            self.assertEqual(self._count("checkout_start"), 1)
            self.assertEqual(self._count("checkout_start", src="card"), 1)
            report_id = session.client_reference_id
            self.assertEqual(self._rows("checkout_start")[0]["report_id"], report_id)

            unpaid = self.client.get("/reports/success?session_id=" + session.id, headers=human)
            self.assertEqual(unpaid.status_code, 402)
            self.assertEqual(self._count("purchase_complete"), 0)

            session.payment_status = "paid"
            session.metadata = {"report_id": report_id}
            unlocked = self.client.get("/reports/success?session_id=" + session.id, headers=human)
            self.assertEqual(unlocked.status_code, 200, unlocked.text)
            self.assertEqual(self._count("purchase_complete"), 1)
            self.assertEqual(self._rows("purchase_complete")[0]["src"], "card")
            self.assertEqual(self._rows("purchase_complete")[0]["report_id"], report_id)

            again = self.client.get("/reports/success?session_id=" + session.id, headers=human)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(self._count("purchase_complete"), 1)

            raw = json.dumps({
                "id": "evt_funnel_1",
                "type": "checkout.session.completed",
                "data": {"object": {"id": session.id, "object": "checkout.session"}},
            }).encode()
            hooked = self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={
                    "Stripe-Signature": _sign("whsec_test", raw),
                    "Content-Type": "application/json",
                    "User-Agent": "Stripe/1.0",
                },
            )
            self.assertEqual(hooked.status_code, 200, hooked.text)
            self.assertEqual(self._count("purchase_complete"), 1)

        bot_session = Session()
        bot_session.id = "cs_test_funnelbot"
        bot_session.url = "https://checkout.stripe.com/c/pay/cs_test_funnelbot"

        def bot_create(**kwargs):
            bot_session.metadata = dict(kwargs.get("metadata") or {})
            bot_session.client_reference_id = kwargs.get("client_reference_id")
            return bot_session

        with mock.patch("stripe.checkout.Session.create", bot_create):
            bot_started = self.client.post(
                "/api/reports/checkout",
                json=INTAKE,
                headers={"User-Agent": "curl/8.4.0"},
            )
            self.assertEqual(bot_started.status_code, 200, bot_started.text)
        self.assertEqual(self._count("checkout_start"), 1)

    def test_admin_funnel_html_and_json(self):
        now = 1_700_000_000.0
        recent = now - 2 * 86400
        old = now - 31 * 86400
        card_report = "c" * 24
        c = app.db()
        try:
            funnel.ensure_table(c)
            c.executemany(
                "INSERT INTO funnel_events(ts, event, src, report_id) VALUES(?,?,?,?)",
                [
                    (recent, "reports_view", "card", None),
                    (recent, "reports_view", "card", None),
                    (recent, "reports_view", "card", None),
                    (recent, "reports_view", "card", None),
                    (recent, "reports_view", "lab", None),
                    (recent, "reports_view", "home", None),
                    (recent, "reports_view", None, None),
                    (recent, "checkout_start", "card", card_report),
                    (recent, "checkout_start", "card", "d" * 24),
                    (recent, "checkout_start", None, "e" * 24),
                    (recent, "purchase_complete", "card", card_report),
                    (old, "reports_view", "card", None),
                    (old, "purchase_complete", "home", "f" * 24),
                    (recent, "reports_view", "<script>", None),
                ],
            )
            c.commit()
        finally:
            c.close()

        missing = self.client.get("/admin/funnel")
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.get("/admin/funnel", headers={"Authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 401)
        legacy = self.client.get("/admin/funnel", headers={"Authorization": "Bearer test-admin"})
        self.assertEqual(legacy.status_code, 401)

        with mock.patch("funnel.time.time", return_value=now):
            page = self.client.get("/admin/funnel", headers=HDR_D)
            body = self.client.get("/admin/funnel?format=json", headers=HDR_CEO)
            upper = self.client.get("/admin/funnel?format=JSON", headers=HDR_D)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertEqual(page.headers["x-robots-tag"], "noindex")
        self.assertEqual(page.headers["cache-control"], "private, no-store")
        self.assertIn("<table", page.text)
        self.assertIn("Counts by source", page.text)
        self.assertIn("Counts by day and source", page.text)
        self.assertIn("persistent Render disk at /data", page.text)
        self.assertIn("survive redeploy", page.text)
        self.assertNotIn("no persistent disk", page.text)
        self.assertNotIn("reset on each deploy", page.text)
        self.assertIn("37.5%", page.text)
        self.assertIn("33.3%", page.text)
        self.assertIn("&lt;script&gt;", page.text)
        self.assertNotIn("<script>", page.text)
        self.assertNotIn(card_report, page.text)
        self.assertIn('scope="col"', page.text)
        self.assertIn("max-width:640px", page.text)

        self.assertEqual(body.status_code, 200, body.text)
        self.assertIn("application/json", body.headers["content-type"])
        self.assertEqual(upper.json(), body.json())
        payload = body.json()
        self.assertEqual(payload["window_days"], 30)
        self.assertEqual(payload["timezone"], "UTC")
        self.assertIn("persistent Render disk at /data", payload["note"])
        self.assertIn("survive redeploy", payload["note"])
        self.assertNotIn("no persistent disk", payload["note"])
        self.assertNotIn("reset on each deploy", payload["note"])
        self.assertEqual(payload["totals"]["reports_view"], 8)
        self.assertEqual(payload["totals"]["checkout_start"], 3)
        self.assertEqual(payload["totals"]["purchase_complete"], 1)
        self.assertEqual(payload["totals"]["view_to_checkout"], 0.375)
        self.assertEqual(payload["totals"]["checkout_to_purchase"], 0.3333)
        by_src = {row["src"]: row for row in payload["by_src"]}
        self.assertEqual(set(by_src), {"card", "lab", "home", None, "<script>"})
        self.assertEqual(by_src["card"]["reports_view"], 4)
        self.assertEqual(by_src["card"]["checkout_start"], 2)
        self.assertEqual(by_src["card"]["purchase_complete"], 1)
        self.assertEqual(by_src["card"]["view_to_checkout"], 0.5)
        self.assertEqual(by_src["card"]["checkout_to_purchase"], 0.5)
        self.assertEqual(by_src["lab"]["view_to_checkout"], 0.0)
        self.assertIsNone(by_src["lab"]["checkout_to_purchase"])
        self.assertEqual(by_src["home"]["view_to_checkout"], 0.0)
        self.assertEqual(len(payload["by_day"]), 1)
        self.assertEqual(payload["by_day"][0]["reports_view"], 8)
        self.assertTrue(payload["by_day"][0]["by_src"])
        self.assertNotIn("FunnelHuman", body.text)
        self.assertNotIn(card_report, body.text)

    def test_report_links_share_one_style(self):
        home = self.client.get("/").text
        self.assertIn("AI jobs, one role at a time", home)
        self.assertIn('class="btn" href="/roles"', home)
        self.assertEqual(home.count('class="btn"'), 1)
        self.assertIn('class="report-cta" href="/reports?src=home"', home)
        self.assertIn("Get your AI Career Report", home)
        self.assertIn("Speculative — not a job promise", home)
        self.assertIn('href="/roles/data-scientist"', home)
        self.assertIn("Emerging research", home)
        self.assertNotIn("/roles/agent-fleet-steward", home)
        self.assertNotIn("setInterval", home)
        nav = home.split('<nav aria-label="Site">', 1)[1].split("</nav>", 1)[0]
        for href in ("/roles", "/reports", "/requests", "/pricing", "/community"):
            self.assertIn('href="%s"' % href, nav)
        for href in ("/join", "/partners", "/ads", "/about", "/signup"):
            self.assertNotIn('href="%s"' % href, nav)
        self.assertIn('href="/report-cta.css"', home)
        community_page = self.client.get("/community")
        self.assertEqual(community_page.status_code, 200, community_page.text[:200])
        community = community_page.text
        self.assertIn("House bot", community)
        self.assertIn('class="house"', community)
        self.assertIn("let sec='all'", community)
        self.assertNotIn("let sec='careers'", community)
        self.assertIn("Mentor bots post through the API", community)
        self.assertIn("It posts through the API.", community)
        self.assertIn("Its posts are simulated.", community)
        self.assertNotIn("and its posts are simulated", community)
        self.assertNotIn("Career Radar", home)
        self.assertIn("Mentor bots post through the API", home)
        self.assertIn("b.house", home)
        card = community.split("function careerCard", 1)[1].split("function problemCard", 1)[0]
        self.assertIn('class="report-cta" href="/reports?src=card"', card)
        self.assertIn("Get your AI Career Report", card)
        self.assertEqual(card.count("report-cta"), 1)
        lab = self.client.get("/problems/1").text
        self.assertIn('class="report-cta" href="/reports?src=lab"', lab)
        self.assertIn("Get your AI Career Report", lab)
        self.assertIn('href="/report-cta.css"', lab)
        css = self.client.get("/report-cta.css")
        self.assertEqual(css.status_code, 200, css.text)
        self.assertIn("text/css", css.headers["content-type"])
        self.assertIn(".report-cta", css.text)
        self.assertIn("min-height:44px", css.text)
        self.assertIn("max-width:640px", css.text)
        footer = home.split("<footer", 1)[1].split("</footer>", 1)[0]
        self.assertIn('href="/reports"', footer)
        self.assertIn('href="/join"', footer)
        self.assertNotIn("src=home", footer)
        self.assertNotIn("src=card", footer)


if __name__ == "__main__":
    unittest.main()
