"""Career Radar and MindGuild Pro waitlist cards. No Stripe calls and no email."""

import csv
import inspect
import io
import os
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ADMIN_KEY_D"] = "test-admin-d"
os.environ["ADMIN_KEY_CEO"] = "test-admin-ceo"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
for _key in (
    "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
    "WAITLIST_WEBHOOK_URL", "RESEND_API_KEY", "SMTP_HOST",
):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import reports
import waitlist

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR_D = {"Authorization": "Bearer " + KEY_D}
HDR_CEO = {"Authorization": "Bearer " + KEY_CEO}
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ORIGIN = "https://mindguild.dev"
INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}
BANNED = (
    "spots left",
    "hurry",
    "limited time",
    "almost gone",
    "only a few",
    "exclusive access",
    "act now",
    "last chance",
    "be the first",
)


class WaitlistTests(unittest.TestCase):
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
        os.environ.pop("RENDER", None)
        c = app.db()
        try:
            waitlist.ensure(c)
            c.execute("DELETE FROM product_waitlist")
            c.execute("DELETE FROM pro_waitlist")
            c.execute("DELETE FROM funnel_events")
            c.commit()
        finally:
            c.close()

    def _count(self, sql, args=()):
        c = app.db()
        try:
            return c.execute(sql, args).fetchone()[0]
        finally:
            c.close()

    def _join(self, email="ada@example.com", product="career_radar", **extra):
        body = {"email": email, "product": product, "website": ""}
        body.update(extra)
        return self.client.post("/api/waitlist", json=body)

    def _seed(self, n, product="career_radar", price=12, source="pricing"):
        c = app.db()
        try:
            waitlist.ensure(c)
            now = time.time()
            stamp = int(now * 1000)
            for i in range(n):
                c.execute(
                    """INSERT INTO product_waitlist(email, product, price_usd, source, created)
                       VALUES(?,?,?,?,?)""",
                    (f"seed-{stamp}-{product}-{i}@example.com", product, price, source, now + i),
                )
            c.commit()
        finally:
            c.close()

    def test_cards_are_on_pricing_and_career_report_pages(self):
        sample = self.client.get("/reports/sample")
        self.assertEqual(sample.status_code, 200, sample.text)
        preview = self.client.post("/api/reports/preview", json=INTAKE, headers=HDR_D)
        self.assertEqual(preview.status_code, 200, preview.text)
        report = self.client.get(preview.json()["url"])
        pages = {
            "/pricing": self.client.get("/pricing"),
            "/reports": self.client.get("/reports"),
            "/reports/sample": sample,
            "report": report,
        }
        for name, page in pages.items():
            self.assertEqual(page.status_code, 200, name)
            for text in (
                "Career Radar",
                "MindGuild Pro",
                "$12-29/mo",
                "not built yet",
                "No payment is taken",
                "Join the waitlist",
                'name="website"',
                "/waitlist.js",
            ):
                self.assertIn(text, page.text, name + " " + text)
            lowered = page.text.lower()
            for banned in BANNED:
                self.assertNotIn(banned, lowered, name + " " + banned)
            self.assertNotIn("Synthetica", page.text, name)
        self.assertIn('data-source="pricing"', pages["/pricing"].text)
        self.assertIn('data-source="reports"', pages["/reports"].text)
        self.assertIn('data-source="sample"', pages["/reports/sample"].text)
        self.assertIn('data-source="report"', report.text)
        token = preview.json()["id"]
        self.assertNotIn('data-source="' + token + '"', report.text)
        self.assertNotIn("people have joined", pages["/pricing"].text)
        self.assertNotIn("person has joined", pages["/reports"].text)
        radar = pages["/pricing"].text.split('id="waitlist-career-radar"', 1)[1]
        self.assertIn("value=\"12\"", radar)
        self.assertIn("value=\"19\"", radar)
        self.assertIn("value=\"29\"", radar)
        pro = pages["/pricing"].text.split('id="waitlist-pro"', 1)[1].split('id="waitlist-career-radar"', 1)[0]
        self.assertNotIn('name="price"', pro)

    def test_checkout_open_keeps_product_waitlists_and_drops_report_waitlist_copy(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        try:
            page = self.client.get("/reports")
        finally:
            os.environ.pop("STRIPE_SECRET_KEY", None)
            os.environ.pop("STRIPE_PRICE_ID", None)
        self.assertIn("Continue to checkout", page.text)
        self.assertIn("Career Radar", page.text)
        self.assertNotIn("Coming soon. Join the waitlist", page.text)

    def test_signup_stores_price_and_source_and_dedupes(self):
        first = self._join(email="Ada@Example.com", price_usd=19, source="sample")
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json(), {"ok": True})
        again = self._join(email="ada@example.com", price_usd=29, source="pricing")
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(self._count("SELECT COUNT(*) FROM product_waitlist"), 1)
        c = app.db()
        try:
            row = c.execute(
                "SELECT email, product, price_usd, source FROM product_waitlist"
            ).fetchone()
        finally:
            c.close()
        self.assertEqual(tuple(row), ("ada@example.com", "career_radar", 19, "sample"))
        other = self._join(email="ada@example.com", product="pro", source="reports")
        self.assertEqual(other.status_code, 201, other.text)
        self.assertEqual(self._count("SELECT COUNT(*) FROM product_waitlist"), 2)
        self.assertEqual(self._count("SELECT COUNT(*) FROM pro_waitlist WHERE email='ada@example.com'"), 1)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM funnel_events WHERE event='pro_waitlist_signup'"), 1
        )
        self.client.post("/api/pro/waitlist", json={"email": "ada@example.com"})
        self.assertEqual(self._count("SELECT COUNT(*) FROM pro_waitlist"), 1)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM funnel_events WHERE event='pro_waitlist_signup'"), 1
        )
        legacy = self.client.post("/api/pro/waitlist", json={"email": "Bo@Example.com"})
        self.assertEqual(legacy.status_code, 201, legacy.text)
        self.assertEqual(self._count("SELECT COUNT(*) FROM pro_waitlist WHERE email='bo@example.com'"), 1)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) FROM product_waitlist WHERE email='bo@example.com' AND product='pro'"
            ),
            1,
        )

    def test_validation_honeypot_and_no_email_side_effects(self):
        def once(**kwargs):
            app._hits.clear()
            return self._join(**kwargs)

        bad = once(email="not-an-email")
        self.assertEqual(bad.status_code, 422, bad.text)
        newline = once(email="ada@exam\nple.com")
        self.assertEqual(newline.status_code, 422, newline.text)
        long_email = ("a" * 64) + "@" + ("b" * 190) + ".com"
        self.assertGreater(len(long_email), 254)
        self.assertEqual(once(email=long_email).status_code, 422)
        self.assertEqual(once(price_usd=10).status_code, 422)
        self.assertEqual(once(price_usd="19.0").status_code, 422)
        self.assertEqual(once(product="pro", price_usd=12).status_code, 422)
        self.assertEqual(once(source="/reports/secret-token").status_code, 422)
        self.assertEqual(once(product="nope").status_code, 422)
        honeypot = once(email="bee@example.com", website="https://spam.test")
        self.assertEqual(honeypot.status_code, 422, honeypot.text)
        self.assertEqual(self._count("SELECT COUNT(*) FROM product_waitlist"), 0)
        app._hits.clear()
        with mock.patch("reports.notify_waitlist") as notify:
            ok = self._join(email="bee@example.com", price_usd="29", source="reports")
        self.assertEqual(ok.status_code, 201, ok.text)
        notify.assert_not_called()
        self.assertEqual(self._count("SELECT price_usd FROM product_waitlist"), 29)
        page = self.client.get("/pricing")
        self.assertNotIn("bee@example.com", page.text)
        self.assertNotIn("bee@example.com", self.client.get("/reports").text)

    def test_rate_limit_uses_client_ip_and_does_not_store_it(self):
        self.assertEqual(app.LIMITS["product_waitlist"], (5, 3600))
        ip = "203.0.113.44"
        for i in range(5):
            res = self._join(
                email=f"n{i}@example.com",
                source="pricing",
            )
            self.assertEqual(res.status_code, 201, res.text)
        blocked = self.client.post(
            "/api/waitlist",
            json={"email": "blocked@example.com", "product": "pro", "website": ""},
            headers={"X-Forwarded-For": "198.51.100.8", "CF-Connecting-IP": ip},
        )
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("Retry-After", blocked.headers)
        c = app.db()
        try:
            blob = " ".join(
                str(value)
                for row in c.execute("SELECT * FROM product_waitlist")
                for value in tuple(row)
            )
            cols = [row[1] for row in c.execute("PRAGMA table_info(product_waitlist)")]
        finally:
            c.close()
        self.assertNotIn(ip, blob)
        self.assertNotIn("198.51.100.8", blob)
        self.assertFalse(any("ip" in name.lower() for name in cols))

        app._hits.clear()
        os.environ["RENDER"] = "true"
        try:
            for i in range(5):
                res = self.client.post(
                    "/api/waitlist",
                    json={"email": f"r{i}@example.com", "product": "career_radar", "website": ""},
                    headers={"X-Forwarded-For": f"9.9.9.{i}", "CF-Connecting-IP": "203.0.113.5"},
                )
                self.assertEqual(res.status_code, 201, res.text)
            same = self.client.post(
                "/api/waitlist",
                json={"email": "same@example.com", "product": "pro", "website": ""},
                headers={"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "203.0.113.5"},
            )
            self.assertEqual(same.status_code, 429, same.text)
            other = self.client.post(
                "/api/waitlist",
                json={"email": "other@example.com", "product": "pro", "website": ""},
                headers={"X-Forwarded-For": "203.0.113.5", "CF-Connecting-IP": "203.0.113.9"},
            )
            self.assertEqual(other.status_code, 201, other.text)
        finally:
            os.environ.pop("RENDER", None)
        c = app.db()
        try:
            blob = " ".join(
                str(value)
                for row in c.execute("SELECT * FROM product_waitlist")
                for value in tuple(row)
            )
        finally:
            c.close()
        self.assertNotIn("203.0.113.5", blob)
        self.assertNotIn("203.0.113.9", blob)

    def test_public_count_is_real_and_omitted_when_small(self):
        self._seed(9)
        quiet = self.client.get("/reports/sample")
        self.assertNotIn('<p class="wl-count">', quiet.text)
        self.assertNotIn("people have joined", quiet.text)
        self._seed(1)
        shown = self.client.get("/pricing")
        radar = shown.text.split('id="waitlist-career-radar"', 1)[1]
        pro = shown.text.split('id="waitlist-pro"', 1)[1].split('id="waitlist-career-radar"', 1)[0]
        self.assertIn("10 people have joined this waitlist.", radar)
        self.assertNotIn("11 people", radar)
        self.assertNotIn("people have joined", pro)
        self.assertNotIn('<p class="wl-count">', pro)

    def test_admin_counts_csv_and_sitemap(self):
        self._join(email="=1+1@example.com", price_usd=12, source="pricing")
        self._join(email="cara@example.com", product="pro", source="report")
        self._join(email="dee@example.com", price_usd=29, source="sample")
        missing = self.client.get("/api/admin/waitlist")
        self.assertEqual(missing.status_code, 401)
        legacy = self.client.get("/api/admin/waitlist", headers={"Authorization": "Bearer test-admin"})
        self.assertEqual(legacy.status_code, 401)
        self.assertEqual(self.client.get("/api/admin/waitlist.csv").status_code, 401)
        body = self.client.get("/api/admin/waitlist", headers=HDR_D)
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(self.client.get("/api/admin/waitlist", headers=HDR_CEO).status_code, 200)
        payload = body.json()
        self.assertEqual(payload["public_count_min"], 10)
        self.assertIn("not built", payload["note"].lower())
        by_product = {item["product"]: item for item in payload["products"]}
        self.assertEqual(by_product["career_radar"]["signups"], 2)
        self.assertEqual(by_product["pro"]["signups"], 1)
        prices = {item["price_usd"]: item["signups"] for item in by_product["career_radar"]["by_price"]}
        self.assertEqual(prices[12], 1)
        self.assertEqual(prices[19], 0)
        self.assertEqual(prices[29], 1)
        sources = {item["source"]: item["signups"] for item in by_product["career_radar"]["by_source"]}
        self.assertEqual(sources["pricing"], 1)
        self.assertEqual(sources["sample"], 1)
        self.assertEqual(sources["reports"], 0)
        cross = {(row["product"], row["price_usd"], row["source"]): row["signups"] for row in payload["by_price_and_source"]}
        self.assertEqual(cross[("career_radar", 12, "pricing")], 1)
        self.assertEqual(cross[("career_radar", 29, "sample")], 1)
        self.assertEqual(cross[("pro", None, "report")], 1)
        emails = {row["email"] for row in payload["signups"]}
        self.assertEqual(emails, {"=1+1@example.com", "cara@example.com", "dee@example.com"})
        page = self.client.get("/admin/waitlist")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("noindex", page.text.lower())
        self.assertIn("Download CSV", page.text)
        self.assertNotIn("cara@example.com", page.text)
        self.assertNotIn('rel="canonical"', page.text)
        self.assertNotIn("innerHTML", page.text)
        script = self.client.get("/waitlist.js")
        self.assertEqual(script.status_code, 200, script.text)
        self.assertNotIn("innerHTML", script.text)
        self.assertIn("textContent", script.text)
        exported = self.client.get("/api/admin/waitlist.csv", headers=HDR_CEO)
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("text/csv", exported.headers["content-type"])
        self.assertIn("mindguild-waitlist.csv", exported.headers["content-disposition"])
        self.assertIn("noindex", exported.headers.get("x-robots-tag", ""))
        rows = list(csv.reader(io.StringIO(exported.text)))
        self.assertEqual(rows[0], ["id", "email", "product", "price_usd", "source", "created_utc"])
        formula = [row for row in rows if "1+1@example.com" in row[1]][0]
        self.assertEqual(formula[1], "'=1+1@example.com")
        self.assertFalse(formula[1].startswith("="))
        self.assertIn("cara@example.com", exported.text)
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        try:
            locs = [
                node.text
                for node in ET.fromstring(self.client.get("/sitemap.xml").text).findall("sm:url/sm:loc", NS)
            ]
            robots = self.client.get("/robots.txt").text
        finally:
            os.environ.pop("PUBLIC_BASE_URL", None)
        self.assertNotIn(ORIGIN + "/admin/waitlist", locs)
        self.assertFalse(any("/admin" in loc for loc in locs))
        self.assertIn("Disallow: /admin\n", robots)
        for path in ("/", "/pricing", "/reports", "/reports/sample"):
            html = self.client.get(path).text
            self.assertNotIn('href="/admin', html, path)

    def test_backfill_is_idempotent_and_skips_search(self):
        source = inspect.getsource(waitlist.ensure)
        self.assertNotIn("fts", source.lower())
        self.assertNotIn("rebuild", source.lower())
        self.assertNotIn("search.ensure", source)
        self.assertIn("waitlist", inspect.getsource(app.migrate))
        c = app.db()
        try:
            before = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
            c.execute("INSERT INTO pro_waitlist(email, created) VALUES('legacy@example.com', 10)")
            c.commit()
            waitlist.ensure(c)
            waitlist.ensure(c)
            c.commit()
            copied = c.execute(
                """SELECT email, product, price_usd, source FROM product_waitlist
                   WHERE email='legacy@example.com'"""
            ).fetchone()
            self.assertEqual(tuple(copied), ("legacy@example.com", "pro", None, None))
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM product_waitlist WHERE email='legacy@example.com'"
                ).fetchone()[0],
                1,
            )
            c.execute("DROP TABLE product_waitlist")
            c.execute(
                """CREATE TABLE product_waitlist(
                       id INTEGER PRIMARY KEY, email TEXT, product TEXT, created REAL)"""
            )
            c.execute(
                "INSERT INTO product_waitlist(email, product, created) VALUES('old@example.com','pro',1)"
            )
            c.commit()
            waitlist.ensure(c)
            c.commit()
            cols = [row[1] for row in c.execute("PRAGMA table_info(product_waitlist)")]
            self.assertIn("price_usd", cols)
            self.assertIn("source", cols)
            kept = c.execute(
                "SELECT email FROM product_waitlist WHERE email='old@example.com'"
            ).fetchone()
            self.assertIsNotNone(kept)
            names = [row[0] for row in c.execute("SELECT name FROM sqlite_master")]
            self.assertTrue(any(name.startswith("ux_product_waitlist") for name in names))
            after = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
        finally:
            c.close()
        self.assertEqual(before, after)
        js_path = os.path.join(os.path.dirname(__file__), "..", "static", "waitlist.js")
        with open(js_path, encoding="utf-8") as handle:
            js = handle.read()
        self.assertNotIn("stripe", js.lower())
        self.assertNotIn("smtp", inspect.getsource(waitlist).lower())
        self.assertNotIn("resend", inspect.getsource(waitlist).lower())


if __name__ == "__main__":
    unittest.main()
