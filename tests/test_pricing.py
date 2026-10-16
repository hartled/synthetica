"""/pricing page, ad Payment Links, and the MindGuild Pro waitlist. No Stripe calls."""

import os
import tempfile
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
for _key in ("AD_LINK_DAY", "AD_LINK_WEEK", "AD_LINK_SECTION"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import funnel
import pricing


class PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys("test-admin-d", "test-admin-ceo")
        cls.client = TestClient(app.app)

    def setUp(self):
        app._hits.clear()
        c = app.db()
        try:
            funnel.ensure_table(c)
            pricing.ensure_table(c)
            c.execute("DELETE FROM funnel_events")
            c.execute("DELETE FROM pro_waitlist")
            c.commit()
        finally:
            c.close()

    def _count(self, sql):
        c = app.db()
        try:
            return c.execute(sql).fetchone()[0]
        finally:
            c.close()

    def test_page_renders_with_brand_and_tiers(self):
        r = self.client.get("/pricing")
        self.assertEqual(r.status_code, 200)
        for text in ("MindGuild Pro", "$5", "$29", "$99", "$19"):
            self.assertIn(text, r.text)
        self.assertNotIn("Synthetica", r.text)

    def test_api_defaults_to_test_links(self):
        d = self.client.get("/api/pricing").json()
        self.assertEqual(d["mode"], "test")
        self.assertEqual({a["tier"] for a in d["ads"]}, {"day", "week", "section"})
        for a in d["ads"]:
            self.assertTrue(a["url"].startswith("https://buy.stripe.com/"))

    def test_env_overrides_and_rejects_bad_links(self):
        env = {"AD_LINK_DAY": "https://buy.stripe.com/abc123",
               "AD_LINK_WEEK": "https://evil.example/x",
               "AD_LINK_SECTION": "https://buy.stripe.com/def456"}
        with mock.patch.dict(os.environ, env):
            links = pricing.ad_links()
        self.assertEqual(links["day"], "https://buy.stripe.com/abc123")
        self.assertEqual(links["week"], pricing.DEFAULT_LINKS["week"])

    def test_waitlist_stores_once_and_records_event(self):
        for _ in range(2):
            r = self.client.post("/api/pro/waitlist", json={"email": "Builder@Example.com"})
            self.assertEqual(r.status_code, 201)
        self.assertEqual(self._count("SELECT COUNT(*) FROM pro_waitlist WHERE email='builder@example.com'"), 1)
        self.assertEqual(self._count("SELECT COUNT(*) FROM funnel_events WHERE event='pro_waitlist_signup'"), 1)

    def test_waitlist_rejects_bad_email(self):
        r = self.client.post("/api/pro/waitlist", json={"email": "nope"})
        self.assertEqual(r.status_code, 422)

    def test_nav_links_pricing(self):
        for path in ("/", "/ads", "/partners", "/join", "/rules"):
            self.assertIn('href="/pricing"', self.client.get(path).text, path)


if __name__ == "__main__":
    unittest.main()
