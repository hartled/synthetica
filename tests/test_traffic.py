"""Daily traffic counters, sanitized UTM, and the report price line.

Checkout is mocked. No Stripe settings are changed. Counts are aggregates:
no IP, cookie, user agent, or report id is stored.
"""

import inspect
import os
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree as ET

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("PUBLIC_BASE_URL", None)
for _key in (
    "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
    "WAITLIST_WEBHOOK_URL", "ADMIN_KEY", "REPORT_PRICE_DISPLAY",
):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import career_data
import funnel
import role_pages
import traffic

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR_D = {"Authorization": "Bearer " + KEY_D}
HDR_CEO = {"Authorization": "Bearer " + KEY_CEO}
HUMAN = {"User-Agent": "Mozilla/5.0 (human-visitor) FunnelHuman/1.0"}
INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
UTM = {
    "utm_source": "newsletter",
    "utm_medium": "email",
    "utm_campaign": "oct-launch",
    "utm_content": "hero.1",
}


class TrafficTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(KEY_D, KEY_CEO)
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        app._hits.clear()
        self._limits = {name: app.LIMITS.get(name) for name in ("role_snapshot", "product_waitlist", "report")}
        self._flush_batch = traffic._FLUSH_BATCH
        app.LIMITS["role_snapshot"] = (1000, 3600)
        app.LIMITS["product_waitlist"] = (1000, 3600)
        app.LIMITS["report"] = (1000, 3600)
        for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(key, None)
        traffic.reset_memory()
        self.client.cookies.clear()

    def tearDown(self):
        traffic._FLUSH_BATCH = self._flush_batch
        traffic.reset_memory()
        for name, value in self._limits.items():
            if value is None:
                app.LIMITS.pop(name, None)
            else:
                app.LIMITS[name] = value
        for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(key, None)
        c = app.db()
        try:
            traffic.ensure(c)
            c.execute("DELETE FROM traffic_daily")
            for table in ("funnel_events", "role_snapshot_signups", "product_waitlist", "pro_waitlist"):
                c.execute("DELETE FROM " + table)
            c.commit()
        finally:
            c.close()

    def _rows(self):
        traffic.flush()
        c = app.db()
        try:
            found = c.execute(
                """SELECT day, path_group, path_key, utm_source, utm_medium,
                          utm_campaign, utm_content, hits
                   FROM traffic_daily ORDER BY path_group, path_key, utm_source"""
            ).fetchall()
            return [tuple(row) for row in found]
        finally:
            c.close()

    def _hits(self, group, key=None, source=None):
        total = 0
        for row in self._rows():
            if row[1] != group:
                continue
            if key is not None and row[2] != key:
                continue
            if source is not None and row[3] != source:
                continue
            total += row[7]
        return total

    def test_page_views_are_daily_aggregates_without_the_report_id(self):
        pages = (
            ("/reports?src=card", "reports", "index"),
            ("/reports/sample", "reports", "sample"),
            ("/roles", "roles", "index"),
            ("/roles/data-scientist", "roles", "data-scientist"),
        )
        for path, _group, _key in pages:
            res = self.client.get(path, headers=HUMAN)
            self.assertEqual(res.status_code, 200, path)
        preview = self.client.post("/api/reports/preview", json=INTAKE, headers=HDR_D)
        self.assertEqual(preview.status_code, 200, preview.text)
        report_id = preview.json()["id"]
        opened = self.client.get("/reports/" + report_id, headers=HUMAN)
        self.assertEqual(opened.status_code, 200, opened.text)
        rows = self._rows()
        keys = {(row[1], row[2]) for row in rows}
        self.assertIn(("reports", "index"), keys)
        self.assertIn(("reports", "sample"), keys)
        self.assertIn(("reports", "page"), keys)
        self.assertIn(("roles", "index"), keys)
        self.assertIn(("roles", "data-scientist"), keys)
        self.assertNotIn(("reports", "card"), keys)
        blob = " ".join(" ".join(str(cell) for cell in row) for row in rows)
        self.assertNotIn(report_id, blob)
        self.assertEqual(self._hits("reports", "page"), 1)
        title = next(entry["title"] for entry in career_data.REAL_CAREERS if entry["id"] == "data-scientist")
        body = self.client.get("/api/admin/traffic?days=14", headers=HDR_D)
        self.assertEqual(body.status_code, 200, body.text)
        payload = body.json()
        today = next(item for item in payload["by_day"] if item["day"] == payload["until"])
        self.assertGreaterEqual(today["report_views"], 3)
        self.assertGreaterEqual(today["role_views"], 2)
        self.assertEqual(today["roles"]["index"], 1)
        match = next(item for item in today["roles"]["by_role"] if item["slug"] == "data-scientist")
        self.assertEqual(match["title"], title)
        self.assertEqual(match["views"], 1)
        self.assertEqual(len(payload["by_day"]), 14)

    def test_bots_head_health_admin_and_errors_are_not_counted(self):
        bot = self.client.get(
            "/reports?utm_source=newsletter",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"},
        )
        self.assertEqual(bot.status_code, 200)
        self.assertNotIn("mg_utm=", bot.headers.get("set-cookie", ""))
        own = self.client.get("/roles", headers={"User-Agent": "MindGuildReports/1.0"})
        self.assertEqual(own.status_code, 200)
        head = self.client.head("/reports")
        self.assertEqual(head.status_code, 200)
        with mock.patch.object(app, "db", side_effect=AssertionError("db")):
            health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True})
        admin = self.client.get("/admin/traffic")
        self.assertEqual(admin.status_code, 200)
        self.assertIn("noindex", admin.text)
        missing_report = self.client.get("/reports/not-a-real")
        self.assertEqual(missing_report.status_code, 404)
        missing_role = self.client.get("/roles/not-a-role")
        self.assertEqual(missing_role.status_code, 404)
        pdf = self.client.get("/reports/sample.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("application/pdf", pdf.headers["content-type"])
        self.assertEqual(self._rows(), [])

    def test_utm_on_views_is_sanitized_and_first_touch(self):
        long_token = "a" * 65
        ok = "b" * 64
        first = self.client.get(
            "/reports",
            params={
                "utm_source": "newsletter",
                "utm_medium": "email",
                "utm_campaign": "oct-launch",
                "utm_content": "hero.1~ok",
            },
            headers=HUMAN,
        )
        self.assertEqual(first.status_code, 200)
        cookie = first.headers.get("set-cookie", "")
        self.assertIn("mg_utm=newsletter|email|oct-launch|hero.1~ok", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Max-Age=604800", cookie)
        self.assertIn("SameSite=Lax", cookie)
        second = self.client.get("/reports?utm_source=later-campaign", headers=HUMAN)
        self.assertEqual(second.status_code, 200)
        self.assertNotIn("mg_utm=later-campaign", second.headers.get("set-cookie", ""))
        self.client.cookies.clear()
        junk = self.client.get(
            "/roles",
            params={
                "utm_source": "ada@example.com",
                "utm_medium": "hello world",
                "utm_campaign": long_token,
                "utm_content": "bad/value",
            },
        )
        self.assertEqual(junk.status_code, 200)
        self.assertNotIn("mg_utm=", junk.headers.get("set-cookie", ""))
        rows = self._rows()
        report_rows = [row for row in rows if row[1] == "reports"]
        self.assertEqual(len(report_rows), 1)
        self.assertEqual(report_rows[0][2], "index")
        self.assertEqual(report_rows[0][3:7], ("newsletter", "email", "oct-launch", "hero.1~ok"))
        self.assertEqual(report_rows[0][7], 2)
        role_rows = [row for row in rows if row[1] == "roles"]
        self.assertEqual(role_rows[0][3:7], ("", "", "", ""))
        self.assertEqual(traffic.clean_token("Ada Lovelace"), "")
        self.assertEqual(traffic.clean_token(ok), ok)
        self.assertEqual(traffic.clean_token(long_token), "")

    def test_snapshot_and_waitlist_keep_utm_and_the_page_source(self):
        visit = self.client.get("/roles/data-scientist?utm_source=post&utm_campaign=career-radar", headers=HUMAN)
        self.assertEqual(visit.status_code, 200)
        self.assertIn('name="source" value="roles"', visit.text)
        self.assertIn('name="utm_source"', visit.text)
        self.assertIn('src="/utm.js"', visit.text)
        cookie_only = self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "ada@example.com", "website": ""},
            headers=HUMAN,
        )
        self.assertEqual(cookie_only.status_code, 201, cookie_only.text)
        self.client.cookies.clear()
        explicit = self.client.post(
            "/api/roles/statistician/snapshot",
            json={
                "email": "bea@example.com",
                "source": "card",
                "utm_source": "ada@example.com",
                "utm_medium": "social",
                "utm_campaign": "fall",
                "utm_content": "slot-2",
            },
            headers=HUMAN,
        )
        self.assertEqual(explicit.status_code, 201, explicit.text)
        c = app.db()
        try:
            stored = c.execute(
                """SELECT email, role_id, source, utm_source, utm_medium, utm_campaign, utm_content
                   FROM role_snapshot_signups ORDER BY email"""
            ).fetchall()
        finally:
            c.close()
        self.assertEqual(tuple(stored[0]), ("ada@example.com", "data-scientist", "roles", "post", None, "career-radar", None))
        self.assertEqual(tuple(stored[1]), ("bea@example.com", "statistician", "card", None, "social", "fall", "slot-2"))
        again = self.client.post(
            "/api/roles/statistician/snapshot",
            json={"email": "bea@example.com", "utm_source": "replaced"},
            headers=HUMAN,
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertFalse(again.json()["created"])

        radar = self.client.post(
            "/api/waitlist",
            json={"email": "cara@example.com", "product": "career_radar", "price_usd": 19, "source": "reports", **UTM},
            headers=HUMAN,
        )
        self.assertEqual(radar.status_code, 201, radar.text)
        pro = self.client.post(
            "/api/waitlist",
            json={"email": "cara@example.com", "product": "pro", "source": "pricing", "utm_source": "pro-post", "utm_campaign": "wait"},
            headers=HUMAN,
        )
        self.assertEqual(pro.status_code, 201, pro.text)
        dup = self.client.post(
            "/api/waitlist",
            json={"email": "cara@example.com", "product": "pro", "source": "pricing", "utm_source": "other"},
            headers=HUMAN,
        )
        self.assertEqual(dup.status_code, 201, dup.text)
        c = app.db()
        try:
            waits = c.execute(
                """SELECT product, utm_source, utm_medium, utm_campaign, utm_content
                   FROM product_waitlist ORDER BY product"""
            ).fetchall()
            pro_event = c.execute(
                """SELECT utm_source, utm_campaign FROM funnel_events
                   WHERE event='pro_waitlist_signup'"""
            ).fetchall()
        finally:
            c.close()
        self.assertEqual(tuple(waits[0]), ("career_radar", "newsletter", "email", "oct-launch", "hero.1"))
        self.assertEqual(tuple(waits[1]), ("pro", "pro-post", None, "wait", None))
        self.assertEqual(len(pro_event), 1)
        self.assertEqual(tuple(pro_event[0]), ("pro-post", "wait"))
        c = app.db()
        try:
            kept = c.execute(
                "SELECT utm_source, utm_campaign FROM role_snapshot_signups WHERE email='bea@example.com'"
            ).fetchone()
        finally:
            c.close()
        self.assertEqual(tuple(kept), (None, "fall"))

    def test_checkout_start_records_utm_without_counting_failures_or_bots(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"

        class Session:
            def __init__(self):
                self.id = "cs_test_traffic123"
                self.url = "https://checkout.stripe.com/c/pay/cs_test_traffic123"
                self.payment_status = "unpaid"
                self.metadata = {}
                self.client_reference_id = ""

        session = Session()
        created = {"n": 0}

        def stripe_create(**kwargs):
            created["n"] += 1
            session.metadata = dict(kwargs.get("metadata") or {})
            session.client_reference_id = kwargs.get("client_reference_id") or ""
            session.id = "cs_test_traffic%d" % created["n"]
            session.url = "https://checkout.stripe.com/c/pay/" + session.id
            return session

        with mock.patch("stripe.checkout.Session.create", stripe_create):
            bad = self.client.post("/api/reports/checkout", json={"current_job": ""}, headers=HUMAN)
            self.assertEqual(bad.status_code, 422)
            bot = self.client.post(
                "/api/reports/checkout",
                json={**INTAKE, **UTM},
                headers={"User-Agent": "curl/8.4.0"},
            )
            self.assertEqual(bot.status_code, 200, bot.text)
            started = self.client.post(
                "/api/reports/checkout?src=card",
                json={**INTAKE, **UTM},
                headers=HUMAN,
            )
            self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(self._hits("checkout", "start"), 1)
        self.assertEqual(self._hits("checkout", "start", "newsletter"), 1)
        c = app.db()
        try:
            row = c.execute(
                """SELECT src, utm_source, utm_medium, utm_campaign, utm_content
                   FROM funnel_events WHERE event='checkout_start'"""
            ).fetchone()
        finally:
            c.close()
        self.assertEqual(tuple(row), ("card", "newsletter", "email", "oct-launch", "hero.1"))
        self.assertNotIn(session.client_reference_id, " ".join(str(cell) for cell in self._rows()[0]))

    def test_admin_traffic_breaks_down_utm_and_exports_csv(self):
        self.client.get("/reports?utm_source=newsletter&utm_medium=email&utm_campaign=oct&utm_content=hero", headers=HUMAN)
        self.client.get("/roles/data-scientist", headers=HUMAN)
        self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "ada@example.com", **UTM},
            headers=HUMAN,
        )
        self.client.post(
            "/api/waitlist",
            json={"email": "cara@example.com", "product": "career_radar", "source": "reports", "utm_source": "newsletter", "utm_campaign": "oct"},
            headers=HUMAN,
        )
        traffic.note("checkout", "start", ("newsletter", "email", "oct", "hero"))
        missing = self.client.get("/api/admin/traffic")
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(self.client.get("/api/admin/traffic.csv").status_code, 401)
        wrong = self.client.get("/api/admin/traffic", headers={"Authorization": "Bearer not-a-key"})
        self.assertEqual(wrong.status_code, 401)
        body = self.client.get("/api/admin/traffic?days=3", headers=HDR_D)
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(self.client.get("/api/admin/traffic", headers=HDR_CEO).status_code, 200)
        payload = body.json()
        self.assertEqual(payload["days"], 3)
        self.assertEqual(len(payload["by_day"]), 3)
        self.assertIn("not by UTM", payload["note"])
        self.assertEqual(payload["sim_budget"]["post_budget"], 30)
        self.assertEqual(payload["database"]["path"], app.DB)
        self.assertGreaterEqual(payload["database"]["size_bytes"], 0)
        views = payload["by_utm"]["views"]
        match = next(row for row in views if row["utm_source"] == "newsletter" and row["utm_campaign"] == "oct")
        self.assertEqual(match["utm_medium"], "email")
        self.assertEqual(match["utm_content"], "hero")
        self.assertGreaterEqual(match["report_views"], 1)
        self.assertGreaterEqual(match["role_views"], 1)
        snap = payload["by_utm"]["snapshot_signups"]
        self.assertEqual(snap[0]["utm_source"], "newsletter")
        self.assertEqual(snap[0]["utm_campaign"], "oct-launch")
        self.assertEqual(snap[0]["count"], 1)
        wait = payload["by_utm"]["waitlist_signups"]
        self.assertEqual(wait[0]["product"], "career_radar")
        self.assertEqual(wait[0]["utm_source"], "newsletter")
        self.assertEqual(wait[0]["utm_campaign"], "oct")
        checkout = payload["by_utm"]["checkout_starts"]
        self.assertEqual(checkout[0]["utm_source"], "newsletter")
        self.assertEqual(checkout[0]["count"], 1)
        self.assertEqual(payload["totals"]["snapshot_signups"], 1)
        self.assertEqual(payload["totals"]["waitlist_signups"], 1)
        self.assertEqual(payload["totals"]["checkout_starts"], 1)
        csv_res = self.client.get("/api/admin/traffic.csv?days=3", headers=HDR_CEO)
        self.assertEqual(csv_res.status_code, 200, csv_res.text)
        self.assertIn("text/csv", csv_res.headers["content-type"])
        lines = csv_res.text.splitlines()
        self.assertEqual(
            lines[0],
            "kind,day,path_group,path_key,utm_source,utm_medium,utm_campaign,utm_content,count",
        )
        blob = "\n".join(lines)
        self.assertIn("report_view", blob)
        self.assertIn("role_view", blob)
        self.assertIn("checkout_start", blob)
        self.assertIn("snapshot_signup", blob)
        self.assertIn("waitlist_signup", blob)
        self.assertIn("newsletter", blob)
        page = self.client.get("/admin/traffic").text
        for label in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "Snapshot signups", "Waitlist signups", "Checkout starts"):
            self.assertIn(label, page)
        self.assertIn("does not segment by UTM", page)
        funnel_html = funnel.render_admin(funnel.summary())
        self.assertNotIn("utm_", funnel_html)
        sitemap = self.client.get("/sitemap.xml")
        locs = [node.text or "" for node in ET.fromstring(sitemap.text).findall("sm:url/sm:loc", NS)]
        self.assertTrue(locs)
        self.assertFalse(any("/admin" in loc for loc in locs))
        robots = self.client.get("/robots.txt")
        self.assertIn("Disallow: /admin\n", robots.text)
        usage = self.client.get("/api/admin/db", headers=HDR_D)
        self.assertEqual(usage.status_code, 200, usage.text)
        db_body = usage.json()
        self.assertEqual(db_body["sim_budget"]["post_budget"], 30)
        self.assertEqual(db_body["path"], app.DB)
        self.assertIn("report_views", db_body["traffic"]["totals"])
        self.assertIn("snapshot_signups", db_body["traffic"]["totals"])
        self.assertGreaterEqual(db_body["size_bytes"], 0)
        self.assertIn("posts", db_body["tables"])

    def test_a_failed_flush_keeps_the_page_up_and_the_batch(self):
        original = traffic._FLUSH_BATCH
        traffic._FLUSH_BATCH = 1
        try:
            with mock.patch.object(traffic, "_write_batch", side_effect=RuntimeError("disk")):
                res = self.client.get("/reports", headers=HUMAN)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertTrue(traffic._pending)
        finally:
            traffic._FLUSH_BATCH = original
            traffic.reset_memory()

    def test_flushed_counts_survive_clearing_the_memory_batch(self):
        self.client.get("/reports", headers=HUMAN)
        traffic.flush()
        traffic.reset_memory()
        self.assertEqual(self._hits("reports", "index"), 1)
        body = self.client.get("/api/admin/traffic", headers=HDR_D)
        today = next(item for item in body.json()["by_day"] if item["day"] == body.json()["until"])
        self.assertGreaterEqual(today["report_views"], 1)

    def test_schema_changes_are_idempotent_and_skip_search(self):
        source = inspect.getsource(traffic.ensure)
        self.assertNotIn("search.ensure", source)
        self.assertNotIn("fts", source.lower())
        self.assertIn("traffic", inspect.getsource(app.migrate))
        c = app.db()
        try:
            before = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
            traffic.ensure(c)
            traffic.ensure(c)
            c.execute("CREATE TABLE IF NOT EXISTS t_utm_probe(id INTEGER)")
            traffic.ensure_utm_columns(c, "t_utm_probe")
            traffic.ensure_utm_columns(c, "t_utm_probe")
            cols = [row[1] for row in c.execute("PRAGMA table_info(t_utm_probe)")]
            c.execute("DROP TABLE t_utm_probe")
            after = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
            c.commit()
        finally:
            c.close()
        self.assertEqual(cols, ["id", "utm_source", "utm_medium", "utm_campaign", "utm_content"])
        self.assertEqual(before, after)
        with open(os.path.join(os.path.dirname(__file__), "..", "docs", "traffic.md"), encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn("/admin/funnel", doc)
        self.assertIn("does not segment by UTM", doc)
        self.assertNotIn("wiped on deploy", doc)
        self.assertNotIn("reset on each deploy", doc)

    def test_report_and_pricing_buy_areas_say_nineteen_one_time(self):
        page = self.client.get("/reports")
        buy, _, rest = page.text.partition('id="product-waitlists"')
        self.assertIn("$19 one time", buy)
        self.assertNotIn("12-29", buy)
        self.assertNotIn("price being tested", buy)
        self.assertIn("Career Radar only: the price being tested is $12-29/mo", rest)
        self.assertIn("not built yet", rest)
        self.assertIn("No payment is taken", rest)
        self.assertIn('name="utm_source"', page.text)
        self.assertIn('src="/utm.js"', page.text)
        pricing = self.client.get("/pricing")
        report_card, _, radar = pricing.text.partition('id="product-waitlists"')
        self.assertIn("$19 one time", report_card)
        self.assertIn("Not a subscription", report_card)
        self.assertNotIn("12-29", report_card)
        self.assertIn("Career Radar only: the price being tested is $12-29/mo", radar)
        self.assertIn("not built yet", radar)
        self.assertIn("No payment is taken", radar)
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        try:
            live = self.client.get("/reports")
        finally:
            os.environ.pop("STRIPE_SECRET_KEY", None)
            os.environ.pop("STRIPE_PRICE_ID", None)
        live_buy, _, live_rest = live.text.partition('id="product-waitlists"')
        self.assertIn("The price is $19 one time.", live_buy)
        self.assertIn("Continue to checkout", live_buy)
        self.assertNotIn("12-29", live_buy)
        self.assertNotIn("+ applicable tax", live_buy)
        self.assertIn("$12-29/mo", live_rest)
        script = self.client.get("/utm.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("sessionStorage", script.text)
        self.assertIn("mg_utm", script.text)


if __name__ == "__main__":
    unittest.main()
