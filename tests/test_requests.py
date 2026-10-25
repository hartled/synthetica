"""Public Request it / vote board. Emails stay on the admin route."""

import inspect
import os
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_KEY_D"] = "test-admin-d"
os.environ["ADMIN_KEY_CEO"] = "test-admin-ceo"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("PUBLIC_BASE_URL", None)

from fastapi.testclient import TestClient

import app
import requests_board

ADMIN = {"Authorization": "Bearer test-admin-d"}
CEO = {"Authorization": "Bearer test-admin-ceo"}
ORIGIN = "https://mindguild.dev"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PUBLIC_KEYS = {"id", "title", "description", "status", "created_at", "vote_count", "seeded", "by", "voted"}


def _name(prefix):
    return prefix + uuid.uuid4().hex[:8]


class RequestBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys("test-admin-d", "test-admin-ceo")
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        app.bootstrap_named_admin_keys("test-admin-d", "test-admin-ceo")
        os.environ.pop("RENDER", None)
        os.environ.pop("PUBLIC_BASE_URL", None)

    def setUp(self):
        app._hits.clear()
        self.client.cookies.clear()
        os.environ.pop("RENDER", None)
        os.environ.pop("PUBLIC_BASE_URL", None)
        c = app.db()
        try:
            c.execute("DELETE FROM feature_request_votes")
            c.execute("DELETE FROM feature_requests WHERE COALESCE(seeded,0)=0")
            c.execute("UPDATE feature_requests SET vote_count=0, status='open' WHERE seeded=1")
            c.commit()
        finally:
            c.close()

    def tearDown(self):
        app.LIMITS["request_submit"] = requests_board.SUBMIT_LIMIT
        app.LIMITS["request_vote"] = requests_board.VOTE_LIMIT
        os.environ.pop("RENDER", None)
        os.environ.pop("PUBLIC_BASE_URL", None)
        self.client.cookies.clear()

    def _idea(self, **kw):
        body = {
            "title": kw.pop("title", "Export bot posts " + uuid.uuid4().hex[:8]),
            "description": kw.pop(
                "description",
                "Let an owner download posts and replies as a JSON file they keep.",
            ),
        }
        body.update(kw)
        return self.client.post("/api/requests", json=body)

    def _human(self):
        from tests.signup_challenge import attach_challenge

        username = _name("rq")
        res = self.client.post("/api/auth/signup", json=attach_challenge(self.client, {
            "email": username + "@example.com",
            "username": username,
            "password": "testpass1",
        }))
        self.assertEqual(res.status_code, 201, res.text)
        return username

    def _bot(self):
        handle = _name("rb")
        res = self.client.post("/api/bots", json={
            "name": "Request Bot", "handle": handle, "persona": "votes on the board",
        })
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def _votes(self):
        c = app.db()
        try:
            return [dict(row) for row in c.execute("SELECT request_id, voter_key FROM feature_request_votes")]
        finally:
            c.close()

    def test_seeds_are_marked_and_start_at_zero(self):
        self.assertGreaterEqual(len(requests_board.SEEDS), 4)
        self.assertLessEqual(len(requests_board.SEEDS), 6)
        c = app.db()
        try:
            rows = c.execute(
                "SELECT title, description, email, vote_count, seeded, status FROM feature_requests WHERE seeded=1"
            ).fetchall()
        finally:
            c.close()
        self.assertEqual(sorted(r["title"] for r in rows), sorted(title for title, _desc in requests_board.SEEDS))
        for row in rows:
            self.assertEqual(row["vote_count"], 0)
            self.assertEqual(row["seeded"], 1)
            self.assertIsNone(row["email"])
            self.assertEqual(row["status"], "open")
            self.assertNotIn("http://", row["description"])
            self.assertNotIn("https://", row["description"])
        listed = self.client.get("/api/requests").json()["requests"]
        seeded = [item for item in listed if item["seeded"]]
        self.assertEqual(len(seeded), len(requests_board.SEEDS))
        for item in seeded:
            self.assertEqual(item["vote_count"], 0)
            self.assertEqual(item["by"], "MindGuild team")
            self.assertFalse(item["voted"])
            self.assertNotIn("email", item)

    def test_public_list_hides_email_and_sorts(self):
        email = "private-board-9f3a@example.com"
        older = self._idea(title="Older export idea aaa", email=email)
        self.assertEqual(older.status_code, 201, older.text)
        self.assertNotIn("email", older.json())
        self.assertNotIn(email, older.text)
        newer = self._idea(title="Newer digest idea bbb")
        self.assertEqual(newer.status_code, 201, newer.text)
        self.assertEqual(self.client.post("/api/requests/%s/vote" % older.json()["id"]).status_code, 200)
        by_votes = self.client.get("/api/requests?sort=votes")
        self.assertEqual(by_votes.headers["cache-control"], "no-store")
        ids = [item["id"] for item in by_votes.json()["requests"]]
        self.assertLess(ids.index(older.json()["id"]), ids.index(newer.json()["id"]))
        self.assertNotIn(email, by_votes.text)
        by_new = self.client.get("/api/requests?sort=new").json()["requests"]
        self.assertEqual(by_new[0]["id"], newer.json()["id"])
        self.assertLess(
            [item["id"] for item in by_new].index(newer.json()["id"]),
            [item["id"] for item in by_new].index(older.json()["id"]),
        )
        self.assertEqual(self.client.get("/api/requests?sort=hot").status_code, 422)
        for item in by_votes.json()["requests"]:
            self.assertEqual(set(item), PUBLIC_KEYS)

    def test_submit_validates_honeypot_profanity_links_and_duplicates(self):
        app.LIMITS["request_submit"] = (100, 3600)
        honeypot = self._idea(website="http://spam.example")
        self.assertEqual(honeypot.status_code, 422, honeypot.text)
        self.assertEqual(self._idea(title="abcd").status_code, 422)
        self.assertEqual(self._idea(title="x" * 121).status_code, 422)
        self.assertEqual(self._idea(description="too short").status_code, 422)
        self.assertEqual(self._idea(description="y" * 1501).status_code, 422)
        self.assertEqual(self._idea(email="not-an-email").status_code, 422)
        self.assertEqual(self._idea(email=123).status_code, 422)
        self.assertEqual(self._idea(title=123, description=123).status_code, 422)
        self.assertEqual(self.client.post("/api/requests", json={}).status_code, 422)
        slur = self._idea(
            title="Please fuck the layout of the feed",
            description="A real description of a layout change that is long enough to pass.",
        )
        self.assertEqual(slur.status_code, 422, slur.text)
        town = self._idea(
            title="Scunthorpe classic assistant",
            description="A classic assistant that helps Scunthorpe readers find Career Cards.",
        )
        self.assertEqual(town.status_code, 201, town.text)
        linked = self._idea(title="Read https://evil.example/now please")
        self.assertEqual(linked.status_code, 422, linked.text)
        two = self._idea(
            description="See https://example.com/a and https://example.com/b for the same export idea details.",
        )
        self.assertEqual(two.status_code, 422, two.text)
        one = self._idea(
            title="Export with one citation link",
            description="Owners download their posts as JSON. Background: https://example.com/export-notes for the file shape.",
        )
        self.assertEqual(one.status_code, 201, one.text)
        violent = self._idea(
            title="A calmer feed ranking",
            description="I will kill you if the ranking stays like this for another week of posts.",
        )
        self.assertEqual(violent.status_code, 422, violent.text)
        title = "Shared export title " + uuid.uuid4().hex[:6]
        self.assertEqual(self._idea(title=title).status_code, 201)
        again = self._idea(title="  " + title.upper() + "!!!")
        self.assertEqual(again.status_code, 409, again.text)
        seeded = self._idea(title=requests_board.SEEDS[0][0] + "!!!")
        self.assertEqual(seeded.status_code, 409, seeded.text)
        xss = self._idea(
            title="<img src=x onerror=alert(1)>Weekly export button",
            description="Let owners download <script>alert(1)</script> their posts as JSON safely.",
        )
        self.assertEqual(xss.status_code, 201, xss.text)
        self.assertNotIn("<", xss.json()["title"])
        self.assertNotIn("<", xss.json()["description"])
        self.assertNotIn(xss.json()["title"], self.client.get("/requests").text)

    def test_one_vote_per_human_bot_or_anonymous_hash(self):
        made = self._idea()
        self.assertEqual(made.status_code, 201, made.text)
        rid = made.json()["id"]
        missing = self.client.post("/api/requests/999999/vote")
        self.assertEqual(missing.status_code, 404, missing.text)
        username = self._human()
        first = self.client.post("/api/requests/%s/vote" % rid)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["vote_count"], 1)
        self.assertTrue(first.json()["voted"])
        again = self.client.post("/api/requests/%s/vote" % rid, headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(self.client.get("/api/requests").json()["requests"][0]["vote_count"] >= 1, True)
        listed = {item["id"]: item for item in self.client.get("/api/requests?limit=100").json()["requests"]}
        self.assertEqual(listed[rid]["vote_count"], 1)
        self.assertTrue(listed[rid]["voted"])
        bot = self._bot()
        bot_vote = self.client.post(
            "/api/requests/%s/vote" % rid,
            headers={"Authorization": "Bearer " + bot["api_key"]},
        )
        self.assertEqual(bot_vote.status_code, 200, bot_vote.text)
        self.assertEqual(bot_vote.json()["vote_count"], 2)
        bot_again = self.client.post(
            "/api/requests/%s/vote" % rid,
            headers={"Authorization": "Bearer " + bot["api_key"], "X-Forwarded-For": "198.51.100.8"},
        )
        self.assertEqual(bot_again.status_code, 409, bot_again.text)
        self.assertEqual(
            self.client.post("/api/admin/humans/%s/ban" % username, headers=ADMIN).status_code, 200
        )
        denied_human = self.client.post("/api/requests/%s/vote" % rid)
        self.assertEqual(denied_human.status_code, 403, denied_human.text)
        self.client.cookies.clear()
        anon = self.client.post("/api/requests/%s/vote" % rid, headers={"User-Agent": "RequestBoardTestUA/1"})
        self.assertEqual(anon.status_code, 200, anon.text)
        other = self.client.post("/api/requests/%s/vote" % rid, headers={"User-Agent": "RequestBoardTestUA/2"})
        self.assertEqual(other.status_code, 200, other.text)
        same = self.client.post("/api/requests/%s/vote" % rid, headers={"User-Agent": "RequestBoardTestUA/2"})
        self.assertEqual(same.status_code, 409, same.text)
        spoof = self.client.post(
            "/api/requests/%s/vote" % rid,
            headers={"User-Agent": "RequestBoardTestUA/2", "X-Forwarded-For": "203.0.113.50"},
        )
        self.assertEqual(spoof.status_code, 409, spoof.text)
        bad = self.client.post("/api/requests/%s/vote" % rid, headers={"Authorization": "Bearer not-a-real-key"})
        self.assertEqual(bad.status_code, 401, bad.text)
        keys = [row["voter_key"] for row in self._votes() if row["request_id"] == rid]
        self.assertTrue(any(key.startswith("human:") for key in keys))
        self.assertTrue(any(key.startswith("bot:") for key in keys))
        self.assertTrue(any(key.startswith("anon:") for key in keys))
        blob = " ".join(keys)
        self.assertNotIn("203.0.113.50", blob)
        self.assertNotIn("203.0.113.9", blob)
        self.assertNotIn("RequestBoardTestUA", blob)
        self.assertNotIn("testclient", blob)
        self.assertEqual(
            self.client.post("/api/admin/bots/%s/ban" % bot["handle"], headers=ADMIN).status_code, 200
        )
        denied = self.client.post(
            "/api/requests/%s/vote" % rid,
            headers={"Authorization": "Bearer " + bot["api_key"]},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_render_trusts_cloudflare_ip_only(self):
        made = self._idea()
        rid = made.json()["id"]
        os.environ["RENDER"] = "true"
        try:
            first = self.client.post("/api/requests/%s/vote" % rid, headers={
                "CF-Connecting-IP": "198.51.100.10",
                "X-Forwarded-For": "10.1.1.1",
                "User-Agent": "RenderVote/1",
            })
            self.assertEqual(first.status_code, 200, first.text)
            second = self.client.post("/api/requests/%s/vote" % rid, headers={
                "CF-Connecting-IP": "198.51.100.11",
                "X-Forwarded-For": "10.1.1.1",
                "User-Agent": "RenderVote/1",
            })
            self.assertEqual(second.status_code, 200, second.text)
            repeat = self.client.post("/api/requests/%s/vote" % rid, headers={
                "CF-Connecting-IP": "198.51.100.10",
                "X-Forwarded-For": "203.0.113.77",
                "User-Agent": "RenderVote/1",
            })
            self.assertEqual(repeat.status_code, 409, repeat.text)
        finally:
            os.environ.pop("RENDER", None)
        made2 = self._idea(title="Off render proxy check " + uuid.uuid4().hex[:6])
        rid2 = made2.json()["id"]
        self.assertEqual(self.client.post("/api/requests/%s/vote" % rid2, headers={
            "CF-Connecting-IP": "198.51.100.20", "User-Agent": "OffRender/1",
        }).status_code, 200)
        self.assertEqual(self.client.post("/api/requests/%s/vote" % rid2, headers={
            "CF-Connecting-IP": "198.51.100.21", "User-Agent": "OffRender/1",
        }).status_code, 409)
        blob = " ".join(row["voter_key"] for row in self._votes())
        self.assertNotIn("198.51.100.10", blob)
        self.assertNotIn("198.51.100.11", blob)
        self.assertNotIn("203.0.113.77", blob)

    def test_rate_limits_use_the_client_ip_bucket(self):
        app.LIMITS["request_submit"] = (2, 3600)
        self.assertEqual(self._idea().status_code, 201)
        self.assertEqual(self._idea().status_code, 201)
        blocked = self._idea()
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("retry-after", {k.lower() for k in blocked.headers})
        app._hits.clear()
        app.LIMITS["request_vote"] = (2, 3600)
        rid = self._idea().json()["id"]
        self.assertEqual(self.client.post("/api/requests/%s/vote" % rid, headers={"User-Agent": "Limit/1"}).status_code, 200)
        self.assertEqual(self.client.post("/api/requests/%s/vote" % rid, headers={"User-Agent": "Limit/2"}).status_code, 200)
        limited = self.client.post("/api/requests/%s/vote" % rid, headers={
            "User-Agent": "Limit/3", "X-Forwarded-For": "198.51.100.99",
        })
        self.assertEqual(limited.status_code, 429, limited.text)

    def test_admin_lists_emails_and_sitemap_skips_admin(self):
        email = "Ada@Example.com"
        made = self._idea(email=email)
        self.assertEqual(made.status_code, 201, made.text)
        rid = made.json()["id"]
        self.assertEqual(self.client.get("/api/admin/requests").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/requests", headers={"Authorization": "Bearer test-admin"}).status_code, 401)
        listed = self.client.get("/api/admin/requests", headers=ADMIN)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(self.client.get("/api/admin/requests", headers=CEO).status_code, 200)
        found = [item for item in listed.json()["requests"] if item["id"] == rid][0]
        self.assertEqual(found["email"], "ada@example.com")
        self.assertIn("vote_count", found)
        self.assertNotIn("voted", found)
        self.assertNotIn("ada@example.com", self.client.get("/api/requests").text)
        changed = self.client.post("/api/admin/requests/%s/status" % rid, headers=CEO, json={"status": "shipped"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["status"], "shipped")
        self.assertEqual(changed.json()["email"], "ada@example.com")
        public = {item["id"]: item for item in self.client.get("/api/requests?limit=100").json()["requests"]}
        self.assertEqual(public[rid]["status"], "shipped")
        self.assertNotIn("email", public[rid])
        self.assertEqual(self.client.post("/api/admin/requests/%s/status" % rid, json={"status": "open"}).status_code, 401)
        self.assertEqual(self.client.post("/api/admin/requests/%s/status" % rid, headers=ADMIN, json={"status": "nope"}).status_code, 422)
        c = app.db()
        try:
            details = " ".join((row[0] or "") for row in c.execute(
                "SELECT detail FROM security_audit_log WHERE action='request_status'"
            ))
        finally:
            c.close()
        self.assertNotIn("ada@example.com", details)
        page = self.client.get("/admin/requests")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("noindex", page.text)
        self.assertNotIn("ada@example.com", page.text)
        self.assertNotIn('rel="canonical"', page.text)
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        try:
            site = self.client.get("/sitemap.xml")
            locs = [node.text for node in ET.fromstring(site.text).findall("sm:url/sm:loc", NS)]
        finally:
            os.environ.pop("PUBLIC_BASE_URL", None)
        self.assertIn(ORIGIN + "/requests", locs)
        self.assertNotIn(ORIGIN + "/admin", locs)
        self.assertNotIn(ORIGIN + "/admin/requests", locs)
        self.assertFalse(any("/admin" in loc for loc in locs))
        home = self.client.get("/").text
        self.assertNotIn('href="/admin', home)

    def test_pages_link_the_board_and_escape_on_the_client(self):
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        try:
            page = self.client.get("/requests?utm=1")
        finally:
            os.environ.pop("PUBLIC_BASE_URL", None)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('rel="canonical" href="https://mindguild.dev/requests"', page.text)
        self.assertNotIn("utm=1", page.text.split("</head>", 1)[0].split('rel="canonical"', 1)[-1][:80])
        self.assertIn("Seeded by the MindGuild team", page.text)
        self.assertIn('aria-current="page"', page.text)
        self.assertIn("<label", page.text)
        self.assertIn('for="website"', page.text)
        self.assertIn("@media(max-width:768px)", page.text)
        self.assertIn("min-height:44px", page.text)
        self.assertEqual(self.client.head("/requests").status_code, 200)
        js = self.client.get("/requests.js").text
        self.assertIn("textContent", js)
        self.assertIn("/api/requests", js)
        self.assertIn("setInterval", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("document.write", js)
        admin_js = open(os.path.join(os.path.dirname(__file__), "..", "static", "admin-requests.html"), encoding="utf-8").read()
        self.assertNotIn("innerHTML", admin_js)
        self.assertNotIn("mindguild.dev", open(os.path.join(os.path.dirname(__file__), "..", "requests_board.py"), encoding="utf-8").read())
        paths = ("/", "/join", "/partners", "/pricing", "/ads", "/about", "/rules", "/search", "/reports", "/refunds", "/signup", "/messages", "/requests")
        for path in paths:
            html = self.client.get(path).text
            nav = html.split("<nav", 1)[1].split("</nav>", 1)[0]
            footer = html.split("<footer", 1)[1].split("</footer>", 1)[0]
            self.assertIn('href="/requests"', nav, path)
            self.assertIn('href="/requests"', footer, path)
            self.assertNotIn("/admin", nav, path)
            self.assertNotIn("/admin", footer, path)

    def test_ensure_is_idempotent_and_does_not_touch_search(self):
        source = inspect.getsource(requests_board.ensure)
        self.assertNotIn("fts_", source.lower())
        self.assertNotIn("search.ensure", source)
        self.assertNotIn("rebuild", source.lower())
        made = self._idea()
        rid = made.json()["id"]
        self.assertEqual(self.client.post("/api/requests/%s/vote" % rid).status_code, 200)
        c = app.db()
        try:
            before = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
        finally:
            c.close()
        requests_board._ready = False
        app.migrate()
        c = app.db()
        try:
            requests_board._ready = False
            requests_board.ensure(c)
            requests_board._ready = False
            requests_board.ensure(c)
            after = [
                (row[0], row[1])
                for row in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts%' OR name LIKE 'search%' ORDER BY name"
                )
            ]
            count = c.execute("SELECT vote_count FROM feature_requests WHERE id=?", (rid,)).fetchone()[0]
            seeds = c.execute("SELECT COUNT(*) FROM feature_requests WHERE seeded=1").fetchone()[0]
            dupes = c.execute(
                "SELECT title_key, COUNT(*) n FROM feature_requests GROUP BY title_key HAVING n>1"
            ).fetchall()
        finally:
            c.close()
        self.assertEqual(before, after)
        self.assertEqual(count, 1)
        self.assertEqual(seeds, len(requests_board.SEEDS))
        self.assertEqual(dupes, [])


if __name__ == "__main__":
    unittest.main()
