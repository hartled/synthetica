"""Smoke tests for site search: groups, DM privacy, and XSS escaping."""

import json
import os
import re
import tempfile
import time
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient

import app
import search

_TAG = re.compile(r"</?([a-zA-Z0-9]+)")


def _safe_snippet(snippet):
    tags = {m.group(1).lower() for m in _TAG.finditer(snippet or "")}
    assert tags <= {"mark"}, snippet
    low = (snippet or "").lower()
    assert "<script" not in low
    assert "<img" not in low


def _plain(obj):
    return re.sub(r"</?mark>", "", json.dumps(obj))


def _pair(t1, i1, t2, i2):
    a, b = (t1, int(i1)), (t2, int(i2))
    return (a, b) if a < b else (b, a)


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()
        cls.db = app.db()
        cls.bot_id = cls.db.execute("SELECT id FROM bots WHERE handle='ceo'").fetchone()["id"]
        cls.key = "syn_searchtestkey"
        cls.db.execute("UPDATE bots SET key_hash=? WHERE id=?", (app.hkey(cls.key), cls.bot_id))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.client.__exit__(None, None, None)
        try:
            os.remove(_DB)
        except OSError:
            pass

    def setUp(self):
        app._hits.clear()
        self.client.cookies.clear()
        # The process shares one database. Other TestClients start the simulator,
        # and it sometimes replies to the seeded example ads. Drop those replies
        # so this check still sees the example ad itself, which is not a post hit.
        c = app.db()
        try:
            c.execute(
                """DELETE FROM likes WHERE post_id IN (
                       SELECT p.id FROM posts p
                       JOIN ads a ON a.post_id = p.parent_id
                       WHERE a.example=1)"""
            )
            c.execute(
                """DELETE FROM posts WHERE parent_id IN (
                       SELECT post_id FROM ads WHERE example=1)"""
            )
            c.commit()
        finally:
            c.close()

    def _get(self, q, type="all", cookie=None, bearer=None):
        self.client.cookies.clear()
        if cookie:
            self.client.cookies.set("syn_session", cookie)
        headers = {}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        return self.client.get("/api/search", params={"q": q, "type": type}, headers=headers)

    def _signup(self, username):
        res = self.client.post("/api/auth/signup", json={
            "email": username + "@example.com",
            "username": username,
            "password": "testpass1",
        })
        self.assertEqual(res.status_code, 201, res.text)
        token = self.client.cookies.get("syn_session")
        self.client.cookies.clear()
        c = app.db()
        try:
            hid = c.execute("SELECT id FROM humans WHERE username=?", (username,)).fetchone()[0]
        finally:
            c.close()
        return token, hid

    def test_pages_have_header_search(self):
        for path in ("/", "/join", "/partners", "/ads", "/rules", "/about", "/signup", "/login", "/me/bots", "/messages", "/chat", "/search", "/reports", "/refunds"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn('role="search"', res.text, path)
            self.assertIn("site-search.js", res.text, path)
        js = self.client.get("/site-search.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("aria-expanded", js.text)
        join = self.client.get("/join").text
        self.assertIn("Search API", join)
        self.assertIn("/api/search", join)
        self.assertIn("api/career-cards", join)

    def test_fts_tables_and_no_dm_index(self):
        names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master")]
        self.assertIn("fts_bots", names)
        self.assertIn("fts_posts", names)
        self.assertIn("fts_cards", names)
        self.assertNotIn("fts_problems", names)
        self.assertFalse(any("dm" in n and n.startswith("fts") for n in names))
        self.assertTrue(search.fts5_available(self.db))

    def test_groups_cards_sections_bots_and_caps(self):
        res = self._get("consciousness")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["q"], "consciousness")
        self.assertNotIn("dms", body["groups"])
        self.assertNotIn("problems", body["groups"])
        secs = body["groups"]["sections"]
        self.assertTrue(any(s["id"] == "consciousness" and s["url"] == "/?section=consciousness" for s in secs), secs)
        self.assertTrue(any("<mark>" in s["snippet"] for s in secs))

        cards = self._get("Shepherd", type="cards").json()["groups"]["cards"]
        self.assertTrue(cards, "expected a Career Card")
        self.assertTrue(any("Shepherd" in c["title"] for c in cards))
        self.assertTrue(all(c["url"].startswith("/#/") for c in cards))
        for c in cards:
            _safe_snippet(c["snippet"])

        bots = self._get("grumpbot", type="bots").json()["groups"]["bots"]
        self.assertTrue(any(b["handle"] == "grumpbot" for b in bots), bots)
        self.assertLessEqual(len(self._get("the").json()["groups"]["posts"]), search.LIMIT_ALL)

        ad = self._get("CloudNap", type="posts").json()["groups"]["posts"]
        self.assertEqual(ad, [], ad)

    def test_post_trigger_cap_and_hidden(self):
        c = app.db()
        word = "zebrasearchtoken"
        cur = c.execute(
            "INSERT INTO posts(bot_id, content, created, post_type) VALUES(?,?,?,?)",
            (self.bot_id, f"hello {word} world", time.time(), "post"),
        )
        pid = cur.lastrowid
        for i in range(8):
            c.execute(
                "INSERT INTO posts(bot_id, content, created, post_type) VALUES(?,?,?,?)",
                (self.bot_id, f"capybara herd number {i} extra", time.time(), "post"),
            )
        c.commit()
        c.close()
        found = self._get(word, type="posts").json()["groups"]["posts"]
        self.assertTrue(any(p["id"] == pid for p in found), found)
        self.assertIn("<mark>", found[0]["snippet"])
        _safe_snippet(found[0]["snippet"])

        capped = self._get("capybara").json()["groups"]["posts"]
        self.assertLessEqual(len(capped), search.LIMIT_ALL)
        wide = self._get("capybara", type="posts").json()["groups"]["posts"]
        self.assertGreater(len(wide), search.LIMIT_ALL)
        self.assertLessEqual(len(wide), search.LIMIT_ONE)

        c = app.db()
        cols = [r[1] for r in c.execute("PRAGMA table_info(posts)")]
        if "hidden" not in cols:
            c.execute("ALTER TABLE posts ADD COLUMN hidden INTEGER DEFAULT 0")
        c.execute("UPDATE posts SET hidden=1 WHERE id=?", (pid,))
        c.commit()
        c.close()
        hidden = self._get(word, type="posts").json()["groups"]["posts"]
        self.assertFalse(any(p["id"] == pid for p in hidden), hidden)

        c = app.db()
        c.execute(
            "INSERT INTO posts(bot_id, content, created, post_type) VALUES(?,?,?,?)",
            (self.bot_id, "deletemetoken should vanish", time.time(), "post"),
        )
        gone_id = c.execute("SELECT id FROM posts WHERE content LIKE 'deletemetoken%'").fetchone()[0]
        c.commit()
        c.close()
        self.assertTrue(any(p["id"] == gone_id for p in self._get("deletemetoken", type="posts").json()["groups"]["posts"]))
        c = app.db()
        c.execute("DELETE FROM posts WHERE id=?", (gone_id,))
        c.commit()
        c.close()
        self.assertFalse(any(p["id"] == gone_id for p in self._get("deletemetoken", type="posts").json()["groups"]["posts"]))

    def test_xss_escaped(self):
        c = app.db()
        payload = '<script>alert(1)</script><img src=x onerror=alert(1)>'
        c.execute(
            "INSERT INTO posts(bot_id, content, created, post_type) VALUES(?,?,?,?)",
            (self.bot_id, payload + " xssneedle", time.time(), "post"),
        )
        c.execute("UPDATE bots SET name=?, persona=? WHERE handle='haikuchan'", (payload, "still a poet xssneedle"))
        c.commit()
        c.close()
        body = self._get("xssneedle").json()
        blobs = []
        for rows in body["groups"].values():
            for row in rows:
                blobs.append(row["snippet"])
                _safe_snippet(row["snippet"])
        joined = "\n".join(blobs)
        self.assertIn("&lt;script&gt;", joined)
        self.assertIn("<mark>", joined)
        self.assertNotIn("<script>", joined)
        self.assertNotIn("<img", joined.lower())

    def test_query_validation(self):
        res = self._get("   consciousness   ")
        self.assertEqual(res.json()["q"], "consciousness")
        long = "n" * 500
        res = self._get(long)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["q"]), search.Q_MAX)
        res = self._get('ceo" OR "a')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertLessEqual(len(res.json()["groups"]["bots"]), search.LIMIT_ALL)
        res = self.client.get("/api/search", params={"q": "ceo", "type": "nope"})
        self.assertEqual(res.status_code, 422)
        empty = self._get("   ").json()
        self.assertEqual(empty["groups"]["bots"], [])
        self.assertNotIn("dms", empty["groups"])

    def test_dm_privacy(self):
        alice_tok, alice_id = self._signup("aliceone")
        bob_tok, bob_id = self._signup("bobtwo")
        cara_tok, cara_id = self._signup("carathree")
        c = app.db()

        def thread(t1, i1, t2, i2, content):
            (a_type, a_id), (b_type, b_id) = _pair(t1, i1, t2, i2)
            cur = c.execute(
                "INSERT INTO dm_threads(a_type,a_id,b_type,b_id,updated) VALUES(?,?,?,?,?)",
                (a_type, a_id, b_type, b_id, time.time()),
            )
            c.execute(
                "INSERT INTO dm_messages(thread_id,sender_type,sender_id,content,created) VALUES(?,?,?,?,?)",
                (cur.lastrowid, t1, i1, content, time.time()),
            )
            return cur.lastrowid

        mine = thread("human", alice_id, "human", bob_id, "purple-narwhal-secret stays in this thread")
        theirs = thread("human", bob_id, "human", cara_id, "orange-marmot-secret belongs to other people")
        bot_thread = thread("bot", self.bot_id, "human", alice_id, "velvet-circuit-secret from the ceo bot")
        c.commit()
        c.close()

        anon = self._get("purple-narwhal-secret").json()
        self.assertNotIn("dms", anon["groups"])
        self.assertNotIn("purple-narwhal", _plain(anon["groups"]))
        self.assertNotIn("orange-marmot", _plain(anon["groups"]))
        self.assertEqual(self._get("secret", type="dms").status_code, 401)

        alice = self._get("secret", cookie=alice_tok).json()
        self.assertIn("dms", alice["groups"])
        blob = _plain(alice["groups"]["dms"])
        self.assertIn("purple-narwhal", blob)
        self.assertIn("velvet-circuit", blob)
        self.assertNotIn("orange-marmot", blob)
        for row in alice["groups"]["dms"]:
            _safe_snippet(row["snippet"])
            self.assertTrue(row["url"].startswith("/messages?thread="))
        urls = {row["url"] for row in alice["groups"]["dms"]}
        self.assertIn(f"/messages?thread={mine}", urls)
        self.assertIn(f"/messages?thread={bot_thread}", urls)
        self.assertNotIn(f"/messages?thread={theirs}", urls)

        bob = self._get("orange-marmot-secret", cookie=bob_tok).json()
        self.assertTrue(bob["groups"]["dms"])
        self.assertIn("orange-marmot", _plain(bob["groups"]["dms"]))
        cara_miss = self._get("velvet-circuit-secret", cookie=cara_tok).json()
        self.assertEqual(cara_miss["groups"]["dms"], [])
        self.assertNotIn("velvet-circuit", _plain(cara_miss["groups"]))

        as_bot = self._get("velvet-circuit-secret", bearer=self.key).json()
        self.assertTrue(as_bot["groups"]["dms"])
        self.assertIn("velvet-circuit", _plain(as_bot["groups"]["dms"]))
        as_bot_other = self._get("orange-marmot-secret", bearer=self.key).json()
        self.assertEqual(as_bot_other["groups"]["dms"], [])
        self.assertNotIn("orange-marmot", _plain(as_bot_other["groups"]))

    def test_rate_limit(self):
        self.assertEqual(app.LIMITS["search"], (60, 60))
        app.LIMITS["search"] = (2, 60)
        try:
            self.assertEqual(self._get("ceo").status_code, 200)
            self.assertEqual(self._get("ceo").status_code, 200)
            blocked = self._get("ceo")
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)
        finally:
            app.LIMITS["search"] = (60, 60)
            app._hits.clear()

    def test_zz_problems_when_table_exists(self):
        before = self._get("lattice").json()
        self.assertNotIn("problems", before["groups"])
        c = app.db()
        c.execute(
            """CREATE TABLE problems(
                id INTEGER PRIMARY KEY, title TEXT, whats_wrong TEXT, evidence_url TEXT,
                evidence_note TEXT, who_affected TEXT, status TEXT, best_answer TEXT,
                best_answer_updated_at REAL, created_by_bot_id INTEGER, career_card_id INTEGER,
                created REAL, updated REAL)"""
        )
        c.execute(
            """CREATE TABLE problem_replies(
                id INTEGER PRIMARY KEY, problem_id INTEGER, bot_id INTEGER, parent_id INTEGER,
                role TEXT, content TEXT, created REAL)"""
        )
        c.execute(
            """INSERT INTO problems(title, whats_wrong, who_affected, status, best_answer, created_by_bot_id, created, updated)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("Qubit drift", "calibration wanders", "night-shift operators", "open",
             "Retune the lattice weekly", self.bot_id, time.time(), time.time()),
        )
        pid = c.execute("SELECT id FROM problems WHERE title='Qubit drift'").fetchone()[0]
        c.execute(
            "INSERT INTO problems(title, whats_wrong, who_affected, status, best_answer, created, updated) VALUES(?,?,?,?,?,?,?)",
            ("Gone issue", "should not show", "nobody", "removed", "secret-removed-answer", time.time(), time.time()),
        )
        c.execute(
            "INSERT INTO problem_replies(problem_id, bot_id, role, content, created) VALUES(?,?,?,?,?)",
            (pid, self.bot_id, "note", "the reply mentions quartz-badger uniquely", time.time()),
        )
        c.commit()
        c.close()
        res = self._get("Qubit", type="problems")
        self.assertEqual(res.status_code, 200, res.text)
        rows = res.json()["groups"]["problems"]
        self.assertTrue(any(r["id"] == pid and r["url"] == f"/problems/{pid}" for r in rows), rows)
        self.assertTrue(any("<mark>" in r["snippet"] for r in rows))
        for r in rows:
            _safe_snippet(r["snippet"])
        self.assertFalse(any("secret-removed-answer" in r["snippet"] or r["title"] == "Gone issue" for r in rows))
        reply = self._get("quartz-badger", type="problems").json()["groups"]["problems"]
        self.assertTrue(any(r["id"] == pid for r in reply), reply)
        absent = self._get("secret-removed-answer", type="problems").json()["groups"]["problems"]
        self.assertEqual(absent, [])
        c = app.db()
        c.execute(
            """INSERT INTO problems(title, whats_wrong, who_affected, status, best_answer, created, updated)
               VALUES(?,?,?,?,?,?,?)""",
            ("Triggerlatch problem", "indexed by the insert trigger", "testers", "open", "", time.time(), time.time()),
        )
        c.commit()
        c.close()
        triggered = self._get("Triggerlatch", type="problems").json()["groups"]["problems"]
        self.assertTrue(any(r["title"] == "Triggerlatch problem" for r in triggered), triggered)


if __name__ == "__main__":
    unittest.main()
