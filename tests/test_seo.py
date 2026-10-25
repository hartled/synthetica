"""Canonical links, robots.txt, and sitemap.xml follow PUBLIC_BASE_URL."""

import os
import re
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("PUBLIC_BASE_URL", None)

from fastapi.testclient import TestClient

import app
import seo

ORIGIN = "https://mindguild.dev"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PAGES = ("/", "/reports", "/pricing", "/partners", "/ads", "/refunds", "/search", "/requests", "/roles")


def _canonical(html):
    found = re.search(r'<link rel="canonical" href="([^"]*)">', html)
    og = re.search(r'<meta property="og:url" content="([^"]*)">', html)
    return (None if found is None else found.group(1), None if og is None else og.group(1))


class SeoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        os.environ.pop("RENDER", None)

    def tearDown(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ.pop("RENDER", None)

    def test_source_does_not_hardcode_a_host(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "seo.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("mindguild.dev", text)
        self.assertNotIn("onrender.com", text)

    def test_public_pages_canonical_ignore_query_and_host(self):
        for path in PAGES:
            # www stays on this host. The retired onrender host redirects, so it
            # cannot be used to read canonical tags. Those tags still name the apex.
            page = self.client.get(path + "?src=card&q=%22%3E%3Cscript%3E", headers={"Host": "www.mindguild.dev"})
            self.assertEqual(page.status_code, 200, path + " " + page.text[:200])
            canonical, og = _canonical(page.text)
            self.assertEqual(canonical, ORIGIN + path, path)
            self.assertEqual(og, canonical, path)
            self.assertNotIn("src=card", canonical)
            self.assertNotIn("<script>", canonical)
            head = self.client.head(path)
            self.assertEqual(head.status_code, 200, path)
            self.assertIn("text/html", head.headers["content-type"])

    def test_about_join_rules_and_problem_pages(self):
        for path in ("/about", "/join", "/rules", "/docs"):
            page = self.client.get(path + "?utm=1")
            canonical, og = _canonical(page.text)
            self.assertEqual(canonical, ORIGIN + path)
            self.assertEqual(og, canonical)
            self.assertEqual(self.client.head(path).status_code, 200)
        c = app.db()
        pid = c.execute(
            "SELECT id FROM problems WHERE status NOT IN ('removed','hidden','deleted') ORDER BY id LIMIT 1"
        ).fetchone()[0]
        c.close()
        page = self.client.get(f"/problems/{pid}?utm=1")
        canonical, og = _canonical(page.text)
        self.assertEqual(canonical, f"{ORIGIN}/problems/{pid}")
        self.assertEqual(og, canonical)
        self.assertEqual(self.client.head(f"/problems/{pid}").status_code, 200)

    def test_unset_base_uses_the_request_origin(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        page = self.client.get("/pricing")
        canonical, og = _canonical(page.text)
        self.assertEqual(canonical, "http://testserver/pricing")
        self.assertEqual(og, canonical)
        self.assertNotIn("mindguild.dev", page.text)
        self.assertNotIn("onrender.com", canonical)

    def test_render_without_public_base_still_serves(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ["RENDER"] = "true"
        page = self.client.get("/ads", headers={"Host": "synthetica-j7zw.onrender.com"})
        self.assertEqual(page.status_code, 200)
        canonical, _og = _canonical(page.text)
        self.assertTrue(canonical.endswith("/ads"))
        self.assertNotIn("mindguild.dev", canonical)

    def test_robots_and_sitemap(self):
        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200, robots.text)
        self.assertIn("text/plain", robots.headers["content-type"])
        self.assertIn("Disallow: /admin\n", robots.text)
        self.assertIn("Disallow: /api\n", robots.text)
        self.assertIn("Sitemap: " + ORIGIN + "/sitemap.xml\n", robots.text)
        self.assertEqual(self.client.head("/robots.txt").status_code, 200)

        site = self.client.get("/sitemap.xml")
        self.assertEqual(site.status_code, 200, site.text)
        self.assertIn("xml", site.headers["content-type"])
        self.assertEqual(self.client.head("/sitemap.xml").status_code, 200)
        root = ET.fromstring(site.text)
        locs = [node.text for node in root.findall("sm:url/sm:loc", NS)]
        for path in PAGES:
            self.assertIn(ORIGIN + path, locs)
        self.assertNotIn(ORIGIN + "/admin", locs)
        self.assertFalse(any("/api" in loc for loc in locs))
        c = app.db()
        handle = c.execute(
            "SELECT handle FROM bots WHERE banned=0 AND handle!='sponsored' ORDER BY handle LIMIT 1"
        ).fetchone()[0]
        pid = c.execute(
            "SELECT id FROM problems WHERE status NOT IN ('removed','hidden','deleted') ORDER BY id LIMIT 1"
        ).fetchone()[0]
        c.close()
        self.assertIn(ORIGIN + "/#/" + handle, locs)
        self.assertNotIn(ORIGIN + "/#/sponsored", locs)
        self.assertIn(ORIGIN + "/problems/" + str(pid), locs)
        for loc in locs:
            path = loc.split(ORIGIN, 1)[-1].split("#", 1)[0].split("?", 1)[0] or "/"
            self.assertEqual(self.client.get(path).status_code, 200, loc)
            self.assertEqual(self.client.head(path).status_code, 200, loc)

    def test_sitemap_skips_banned_bots_and_hidden_problems(self):
        before = [
            node.text
            for node in ET.fromstring(self.client.get("/sitemap.xml").text).findall("sm:url/sm:loc", NS)
        ]
        c = app.db()
        handle = c.execute(
            "SELECT handle FROM bots WHERE banned=0 AND handle!='sponsored' ORDER BY handle LIMIT 1"
        ).fetchone()[0]
        self.assertIn(ORIGIN + "/#/" + handle, before)
        now = time.time()
        c.execute("UPDATE bots SET banned=1 WHERE handle=?", (handle,))
        cur = c.execute(
            "INSERT INTO problems(title, whats_wrong, status, created, updated) VALUES(?,?,?,?,?)",
            ("hidden from sitemap", "not public", "hidden", now, now),
        )
        hidden_id = cur.lastrowid
        c.commit()
        c.close()
        try:
            locs = [
                node.text
                for node in ET.fromstring(self.client.get("/sitemap.xml").text).findall("sm:url/sm:loc", NS)
            ]
            self.assertNotIn(ORIGIN + "/#/" + handle, locs)
            self.assertNotIn(ORIGIN + "/problems/" + str(hidden_id), locs)
        finally:
            c = app.db()
            c.execute("UPDATE bots SET banned=0 WHERE handle=?", (handle,))
            c.execute("DELETE FROM problems WHERE id=?", (hidden_id,))
            c.commit()
            c.close()

    def test_tags_are_escaped(self):
        html = "<head><title>x</title></head>"
        out = seo.inject_canonical(html, 'https://example.com/a&b"\'<script>')
        self.assertIn("https://example.com/a&amp;b&quot;&#39;&lt;script&gt;", out)
        self.assertNotIn("<script>", out.split("<title>", 1)[0])
        self.assertEqual(out.count('rel="canonical"'), 1)
        again = seo.inject_canonical(out, "https://example.com/again")
        self.assertEqual(again, out)

    def test_admin_html_is_not_rewritten(self):
        page = self.client.get("/admin/funnel")
        self.assertIn(page.status_code, (401, 503))
        self.assertNotIn('rel="canonical"', page.text)
