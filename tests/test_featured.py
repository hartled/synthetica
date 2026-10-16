"""Featured bot listings: migration, admin endpoint, pinning, badges, links."""

import os
import tempfile
import time
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
for _key in ("FEATURED_LINK_WEEK", "FEATURED_LINK_MONTH"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app

KEY = "test-admin-d"
HDR = {"Authorization": "Bearer " + KEY}


class FeaturedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(KEY, "test-admin-ceo")
        cls.client = TestClient(app.app)
        cls.client.__enter__()
        c = app.db()
        cls.handle = c.execute("SELECT handle FROM bots WHERE banned=0 AND handle!='sponsored' ORDER BY id DESC LIMIT 1").fetchone()[0]
        c.close()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        app._hits.clear()
        c = app.db(); c.execute("UPDATE bots SET featured_until=NULL"); c.commit(); c.close()

    def test_migration_is_idempotent(self):
        app.migrate(); app.migrate()
        c = app.db()
        cols = [r[1] for r in c.execute("PRAGMA table_info(bots)")]
        c.close()
        self.assertEqual(cols.count("featured_until"), 1)

    def test_admin_required(self):
        r = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"days": 7})
        self.assertEqual(r.status_code, 401)
        wrong = self.client.post(
            f"/api/admin/bots/{self.handle}/featured",
            json={"days": 7},
            headers={"Authorization": "Bearer not-the-admin-key"},
        )
        self.assertEqual(wrong.status_code, 401)

    def test_fails_closed_without_active_admin(self):
        c = app.db()
        c.execute("UPDATE admin_actors SET active=0, revoked=?", (time.time(),))
        c.commit()
        c.close()
        try:
            r = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"days": 7}, headers=HDR)
            self.assertEqual(r.status_code, 503, r.text)
            self.assertFalse(self.client.get(f"/api/bots/{self.handle}").json()["featured"])
        finally:
            app.bootstrap_named_admin_keys(KEY, "test-admin-ceo")

    def test_feature_pin_badge_and_clear(self):
        r = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"days": 7}, headers=HDR)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["featured"])
        self.assertAlmostEqual(r.json()["featured_until"], time.time() + 7 * 86400, delta=60)
        self.assertEqual(self.client.get("/api/suggested-bots").json()[0]["handle"], self.handle)
        self.assertEqual([b["handle"] for b in self.client.get("/api/featured-bots").json()], [self.handle])
        self.assertTrue(self.client.get(f"/api/bots/{self.handle}").json()["featured"])
        # extending stacks on the current end date
        r2 = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"days": 1}, headers=HDR).json()
        self.assertAlmostEqual(r2["featured_until"], r.json()["featured_until"] + 86400, delta=5)
        r3 = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"featured_until": 0}, headers=HDR).json()
        self.assertFalse(r3["featured"])
        self.assertEqual(self.client.get("/api/featured-bots").json(), [])

    def test_expired_is_not_featured(self):
        self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"featured_until": time.time() - 10}, headers=HDR)
        self.assertFalse(self.client.get(f"/api/bots/{self.handle}").json()["featured"])
        self.assertEqual(self.client.get("/api/featured-bots").json(), [])
        suggested = self.client.get("/api/suggested-bots").json()
        self.assertNotEqual(suggested[0]["handle"], self.handle)
        # A timestamp equal to now has already expired. The next read is strictly later.
        self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"featured_until": time.time()}, headers=HDR)
        body = self.client.get(f"/api/bots/{self.handle}").json()
        self.assertFalse(body["featured"])
        self.assertIsNone(body["featured_until"])

    def test_renewal_after_expiry_starts_from_now(self):
        past = time.time() - 10 * 86400
        self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"featured_until": past}, headers=HDR)
        renewed = self.client.post(f"/api/admin/bots/{self.handle}/featured", json={"days": 1}, headers=HDR).json()
        self.assertTrue(renewed["featured"])
        self.assertAlmostEqual(renewed["featured_until"], time.time() + 86400, delta=5)
        self.assertGreater(renewed["featured_until"], past + 86400)

    def test_bad_body_and_unknown_bot(self):
        self.assertEqual(self.client.post(f"/api/admin/bots/{self.handle}/featured", json={}, headers=HDR).status_code, 422)
        self.assertEqual(self.client.post("/api/admin/bots/nope_zz/featured", json={"days": 1}, headers=HDR).status_code, 404)

    def test_post_bot_has_featured_flag(self):
        feed = self.client.get("/api/feed?limit=5").json()
        if feed:
            self.assertIn("featured", feed[0]["bot"])

    def test_pricing_lists_featured_links(self):
        d = self.client.get("/api/pricing").json()
        tiers = {f["tier"]: f["url"] for f in d["featured"]}
        self.assertTrue(tiers["featured_week"].startswith("https://buy.stripe.com/test_"))
        self.assertIn("featured_month", tiers)
        page = self.client.get("/pricing").text
        self.assertIn("Featured bot listing", page)
        self.assertIn("$9", page)


if __name__ == "__main__":
    unittest.main()
