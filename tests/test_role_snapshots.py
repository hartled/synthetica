"""Role pages and the free one-role snapshot. No email is sent."""

import csv
import inspect
import io
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

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
os.environ.pop("PUBLIC_BASE_URL", None)
for _key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
             "WAITLIST_WEBHOOK_URL", "RESEND_API_KEY", "SMTP_HOST"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import career_data
import role_pages

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR_D = {"Authorization": "Bearer " + KEY_D}
HDR_CEO = {"Authorization": "Bearer " + KEY_CEO}
ORIGIN = "https://mindguild.dev"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")


class RoleSnapshotTests(unittest.TestCase):
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
        app.LIMITS["role_snapshot"] = role_pages.SIGNUP_LIMIT
        os.environ.pop("RENDER", None)
        os.environ.pop("PUBLIC_BASE_URL", None)
        c = app.db()
        try:
            role_pages.ensure(c)
            c.execute("DELETE FROM role_snapshot_signups")
            c.commit()
        finally:
            c.close()

    def _count(self, sql, args=()):
        c = app.db()
        try:
            return c.execute(sql, args).fetchone()[0]
        finally:
            c.close()

    def _rows(self):
        c = app.db()
        try:
            return c.execute(
                "SELECT email, role_id, source FROM role_snapshot_signups ORDER BY id"
            ).fetchall()
        finally:
            c.close()

    def test_pages_follow_the_career_report_catalog(self):
        catalog = [entry["id"] for entry in career_data.REAL_CAREERS]
        self.assertEqual(catalog, [entry["id"] for entry in role_pages.roles()])
        self.assertEqual(len(catalog), 11)
        speculative = [entry["id"] for entry in career_data.SPECULATIVE_CAREERS]
        self.assertTrue(speculative)
        self.assertFalse(set(catalog) & set(speculative))
        index = self.client.get("/roles")
        self.assertEqual(index.status_code, 200, index.text[:200])
        self.assertIn("MindGuild", index.text)
        self.assertNotIn("Synthetica", index.text)
        self.assertIn('href="/reports"', index.text)
        self.assertIn("No email is sent", index.text)
        self.assertIn(
            "A free on-page snapshot of the role: what AI work in it involves, "
            "the skills, courses and sources. No score, pay or growth figures.",
            index.text,
        )
        self.assertIn("AI jobs, one role at a time", index.text)
        self.assertNotIn("AI exposure", index.text)
        self.assertNotIn("The social network for synthetic minds", index.text)
        self.assertIn('method="get"', index.text)
        self.assertIn('name="q"', index.text)
        for entry in career_data.REAL_CAREERS:
            self.assertIn(f'href="/roles/{entry["id"]}"', index.text)
            self.assertIn(entry["title"], index.text)
            self.assertIn(entry["label"], index.text)
            page = self.client.get("/roles/" + entry["id"])
            self.assertEqual(page.status_code, 200, entry["id"])
            self.assertIn(entry["title"], page.text)
            self.assertIn(entry["label"], page.text)
            self.assertIn(entry["summary"][:40], page.text)
            self.assertIn('href="/reports"', page.text)
            self.assertIn('name="website"', page.text)
            self.assertIn("No email is sent", page.text)
            self.assertIn("AI jobs, one role at a time", page.text)
            self.assertNotIn("The social network for synthetic minds", page.text)
            self.assertNotIn('id="snapshot"', page.text)
            self.assertNotIn("Your free snapshot", page.text)
        missing = self.client.get("/roles/" + speculative[0])
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn(career_data.SPECULATIVE_CAREERS[0]["summary"][:40], missing.text)
        junk = self.client.get("/roles/not-a-role")
        self.assertEqual(junk.status_code, 404)

    def test_search_is_server_rendered(self):
        hit = self.client.get("/roles", params={"q": "statistician"})
        self.assertEqual(hit.status_code, 200)
        self.assertIn('data-role="statistician"', hit.text)
        self.assertNotRegex(hit.text, r'data-role="statistician"[^>]*hidden')
        self.assertRegex(hit.text, r'data-role="web-developer"[^>]*hidden')
        self.assertIn("1 of 11 roles", hit.text)
        empty = self.client.get("/roles", params={"q": "zzzz-not-a-role"})
        self.assertIn("No roles match that search.", empty.text)
        self.assertIn("0 of 11 roles", empty.text)
        self.assertNotIn('id="none" class="card" hidden', empty.text)
        nasty = self.client.get("/roles", params={"q": '"><script>alert(1)</script>'})
        self.assertNotIn("<script>alert", nasty.text)
        self.assertIn("&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;", nasty.text)

    def test_snapshot_is_on_the_page_and_stores_one_row_per_email_and_role(self):
        first = self.client.post(
            "/roles/data-scientist",
            data={"email": "Ada@Example.com", "website": "", "source": "card"},
        )
        self.assertEqual(first.status_code, 200, first.text[:400])
        self.assertIn('id="snapshot"', first.text)
        self.assertIn("Your free snapshot", first.text)
        self.assertIn("What AI work looks like in this role", first.text)
        self.assertNotIn("AI exposure", first.text)
        self.assertIn("Established", first.text)
        self.assertIn("Skills this career report lists", first.text)
        self.assertIn("Python", first.text)
        self.assertIn("https://www.onetonline.org/link/summary/15-2051.00", first.text)
        self.assertIn("https://www.deeplearning.ai/specializations/machine-learning", first.text)
        self.assertIn(role_pages.OUTLOOK, first.text)
        self.assertIn("No email is sent", first.text)
        self.assertNotIn("Ada@Example.com", first.text)
        self.assertNotIn("ada@example.com", first.text)
        self.assertIsNone(PERCENT.search(role_pages.OUTLOOK))
        before = self.client.get("/roles/data-scientist").text
        self.assertNotIn("deeplearning.ai", before)
        self.assertNotIn("onetonline.org", before)
        again = self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "ada@example.com", "website": "", "source": "other"},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["created"], False)
        self.assertEqual(self._count("SELECT COUNT(*) FROM role_snapshot_signups"), 1)
        self.assertEqual(tuple(self._rows()[0]), ("ada@example.com", "data-scientist", "card"))
        other = self.client.post(
            "/api/roles/statistician/snapshot",
            json={"email": "ada@example.com", "source": "roles"},
        )
        self.assertEqual(other.status_code, 201, other.text)
        body = other.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["snapshot"]["label"], "Established")
        self.assertEqual(body["snapshot"]["title"], "Statistician")
        stat = next(entry for entry in career_data.REAL_CAREERS if entry["id"] == "statistician")
        self.assertEqual(body["snapshot"]["summary"], stat["summary"])
        self.assertNotIn("exposure", body["snapshot"])
        self.assertNotIn("%", body["snapshot"]["outlook"])
        self.assertEqual(self._count("SELECT COUNT(*) FROM role_snapshot_signups"), 2)
        emerging = self.client.post(
            "/api/roles/machine-learning-engineer/snapshot",
            json={"email": "ml@example.com"},
        )
        snap = emerging.json()["snapshot"]
        self.assertEqual(snap["label"], "Emerging research")
        self.assertIn("not its own handbook occupation", snap["gloss"].lower())
        self.assertFalse(snap["speculative"])
        self.assertIsNone(snap["disclaimer"])
        junk_source = self.client.post(
            "/api/roles/web-developer/snapshot",
            json={"email": "web@example.com", "source": "<script>"},
        )
        self.assertEqual(junk_source.status_code, 201, junk_source.text)
        stored = [row for row in self._rows() if row["email"] == "web@example.com"][0]
        self.assertEqual(stored["source"], "roles")

    def test_honeypot_email_and_rate_limit_use_the_trusted_client_ip(self):
        bad = self.client.post("/api/roles/data-scientist/snapshot", json={"email": "nope"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self._count("SELECT COUNT(*) FROM role_snapshot_signups"), 0)
        honey = self.client.post(
            "/roles/data-scientist",
            data={"email": "bot@example.com", "website": "http://spam.example"},
        )
        self.assertEqual(honey.status_code, 422, honey.text)
        self.assertNotIn('id="snapshot"', honey.text)
        self.assertEqual(self._count("SELECT COUNT(*) FROM role_snapshot_signups"), 0)
        app.LIMITS["role_snapshot"] = (2, 3600)
        app._hits.clear()
        for name, forwarded in (("one@example.com", "203.0.113.5"), ("two@example.com", "203.0.113.9")):
            res = self.client.post(
                "/api/roles/data-scientist/snapshot",
                json={"email": name},
                headers={"X-Forwarded-For": forwarded},
            )
            self.assertEqual(res.status_code, 201, res.text)
        blocked = self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "third@example.com"},
            headers={"X-Forwarded-For": "198.51.100.20"},
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self._count("SELECT COUNT(*) FROM role_snapshot_signups"), 2)
        os.environ["RENDER"] = "true"
        try:
            other = self.client.post(
                "/api/roles/statistician/snapshot",
                json={"email": "cf@example.com"},
                headers={"X-Forwarded-For": "203.0.113.5", "CF-Connecting-IP": "198.51.100.8"},
            )
            self.assertEqual(other.status_code, 201, other.text)
        finally:
            os.environ.pop("RENDER", None)
        blob = " ".join(" ".join(str(cell) for cell in row) for row in self._rows())
        self.assertNotIn("203.0.113.5", blob)
        self.assertNotIn("198.51.100", blob)
        cols = []
        c = app.db()
        try:
            cols = [row[1] for row in c.execute("PRAGMA table_info(role_snapshot_signups)")]
        finally:
            c.close()
        self.assertNotIn("ip", cols)

    def test_admin_counts_csv_and_sitemap(self):
        self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "=1+1@example.com", "source": "card"},
        )
        self.client.post(
            "/api/roles/data-scientist/snapshot",
            json={"email": "cara@example.com", "source": "roles"},
        )
        self.assertEqual(self.client.get("/api/admin/snapshots").status_code, 401)
        self.assertEqual(
            self.client.get("/api/admin/snapshots", headers={"Authorization": "Bearer test-admin"}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/admin/snapshots.csv").status_code, 401)
        body = self.client.get("/api/admin/snapshots", headers=HDR_D)
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(self.client.get("/api/admin/snapshots", headers=HDR_CEO).status_code, 200)
        payload = body.json()
        self.assertIn("No email is sent", payload["note"])
        self.assertEqual(payload["total"], 2)
        counts = {item["role_id"]: item["signups"] for item in payload["counts"]}
        self.assertEqual(set(counts), {entry["id"] for entry in career_data.REAL_CAREERS})
        self.assertEqual(counts["data-scientist"], 2)
        self.assertEqual(counts["statistician"], 0)
        emails = {row["email"] for row in payload["signups"]}
        self.assertEqual(emails, {"=1+1@example.com", "cara@example.com"})
        page = self.client.get("/admin/snapshots")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("noindex", page.text.lower())
        self.assertIn("Download CSV", page.text)
        self.assertNotIn("cara@example.com", page.text)
        self.assertNotIn('rel="canonical"', page.text)
        self.assertNotIn("innerHTML", page.text)
        exported = self.client.get("/api/admin/snapshots.csv", headers=HDR_CEO)
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("text/csv", exported.headers["content-type"])
        self.assertIn("mindguild-role-snapshots.csv", exported.headers["content-disposition"])
        self.assertIn("noindex", exported.headers.get("x-robots-tag", ""))
        rows = list(csv.reader(io.StringIO(exported.text)))
        self.assertEqual(rows[0], ["id", "email", "role_id", "role_title", "source", "created_utc"])
        formula = [row for row in rows if "1+1@example.com" in row[1]][0]
        self.assertEqual(formula[1], "'=1+1@example.com")
        self.assertFalse(formula[1].startswith("="))
        self.assertEqual(formula[3], "Data scientist")
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        try:
            site = self.client.get("/sitemap.xml")
            locs = [node.text for node in ET.fromstring(site.text).findall("sm:url/sm:loc", NS)]
            role = self.client.get("/roles/software-developer")
            index = self.client.get("/roles?utm=1")
        finally:
            os.environ.pop("PUBLIC_BASE_URL", None)
        self.assertIn(ORIGIN + "/roles", locs)
        for entry in career_data.REAL_CAREERS:
            self.assertIn(ORIGIN + "/roles/" + entry["id"], locs)
        for entry in career_data.SPECULATIVE_CAREERS:
            self.assertNotIn(ORIGIN + "/roles/" + entry["id"], locs)
        self.assertNotIn(ORIGIN + "/admin/snapshots", locs)
        self.assertFalse(any("/admin" in loc for loc in locs))
        self.assertIn('rel="canonical" href="' + ORIGIN + '/roles/software-developer"', role.text)
        self.assertIn('property="og:url" content="' + ORIGIN + '/roles/software-developer"', role.text)
        self.assertIn('property="og:title"', role.text)
        self.assertIn('rel="canonical" href="' + ORIGIN + '/roles"', index.text)
        self.assertNotIn("utm=1", index.text.split('rel="canonical"', 1)[1][:80])
        home = self.client.get("/")
        self.assertIn('href="/roles"', home.text)
        self.assertNotIn('href="/admin', home.text)

    def test_migration_is_idempotent_and_skips_search(self):
        source = inspect.getsource(role_pages.ensure)
        self.assertNotIn("fts", source.lower())
        self.assertNotIn("rebuild", source.lower())
        self.assertNotIn("search.ensure", source)
        self.assertIn("role_pages", inspect.getsource(app.migrate))
        module = inspect.getsource(role_pages)
        self.assertNotIn("smtp", module.lower())
        self.assertNotIn("resend", module.lower())
        self.assertNotIn("stripe", module.lower())
        c = app.db()
        try:
            before = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
            role_pages.ensure(c)
            role_pages.ensure(c)
            c.execute("DROP TABLE role_snapshot_signups")
            c.execute(
                """CREATE TABLE role_snapshot_signups(
                       id INTEGER PRIMARY KEY, email TEXT, role_id TEXT, created REAL)"""
            )
            c.execute(
                "INSERT INTO role_snapshot_signups(email, role_id, created) VALUES('old@example.com','statistician',1)"
            )
            c.commit()
            role_pages.ensure(c)
            c.commit()
            cols = [row[1] for row in c.execute("PRAGMA table_info(role_snapshot_signups)")]
            self.assertIn("source", cols)
            kept = c.execute(
                "SELECT email FROM role_snapshot_signups WHERE email='old@example.com'"
            ).fetchone()
            self.assertIsNotNone(kept)
            names = [row[0] for row in c.execute("SELECT name FROM sqlite_master")]
            self.assertTrue(any(name.startswith("ux_role_snapshot") for name in names))
            role_pages.ensure(c)
            c.commit()
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM role_snapshot_signups WHERE email='old@example.com'"
                ).fetchone()[0],
                1,
            )
            after = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
        finally:
            c.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
