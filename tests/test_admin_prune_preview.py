"""GET /api/admin/prune/preview is a read-only dry run of scripts/prune_posts.py."""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location(
    "prune_posts", os.path.join(ROOT, "scripts", "prune_posts.py")
)
prune_posts = importlib.util.module_from_spec(_spec)
sys.modules["prune_posts"] = prune_posts
_spec.loader.exec_module(prune_posts)

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_KEY_D"] = "test-admin-d"
os.environ["ADMIN_KEY_CEO"] = "test-admin-ceo"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ["COOKIE_SECURE"] = "false"

import app
from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer test-admin-d"}

SCHEMA = """
CREATE TABLE bots(
    id INTEGER PRIMARY KEY,
    handle TEXT,
    simulated INTEGER DEFAULT 0,
    featured_until REAL
);
CREATE TABLE posts(
    id INTEGER PRIMARY KEY,
    bot_id INTEGER,
    parent_id INTEGER,
    content TEXT,
    created REAL,
    ad_id INTEGER
);
CREATE TABLE likes(bot_id INTEGER, post_id INTEGER, PRIMARY KEY(bot_id, post_id));
CREATE TABLE career_cards(id INTEGER PRIMARY KEY, post_id INTEGER, title TEXT);
CREATE TABLE ads(id INTEGER PRIMARY KEY, post_id INTEGER, title TEXT);
CREATE TABLE problems(id INTEGER PRIMARY KEY, title TEXT, career_card_id INTEGER);
CREATE TABLE sponsorships(id INTEGER PRIMARY KEY, handle TEXT, tier TEXT);
"""


def _long(seed, n=24):
    return " ".join(f"{seed}{i}" for i in range(n))


def _files(path):
    found = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = path + suffix
        if os.path.isfile(candidate):
            with open(candidate, "rb") as handle:
                found[suffix] = handle.read()
        else:
            found[suffix] = None
    return found


class PreviewDoesNotWrite(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.now = time.time()
        self.old = self.now - 30 * 86400
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(1, 'sim', 1)")
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(2, 'real', 0)")
        self.conn.execute(
            "INSERT INTO bots(id, handle, simulated, featured_until) VALUES(3, 'featured', 1, ?)",
            (self.now + 86400,),
        )
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(4, 'sponsor', 0)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def _post(self, bot_id, content, created, parent_id=None, ad_id=None):
        cur = self.conn.execute(
            "INSERT INTO posts(bot_id, content, created, parent_id, ad_id) VALUES(?,?,?,?,?)",
            (bot_id, content, created, parent_id, ad_id),
        )
        return cur.lastrowid

    def test_preview_reports_deletes_and_leaves_the_file_unchanged(self):
        dup = _long("dup")
        keep = self._post(1, dup, self.old)
        extra = self._post(1, dup, self.old + 1)
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(1, ?)", (keep,))
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(3, ?)", (keep,))
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(1, ?)", (extra,))
        quiet = self._post(1, _long("quiet"), self.old)
        self.conn.execute(
            "INSERT INTO career_cards(id, post_id, title) VALUES(1, ?, 'Quiet card')",
            (quiet,),
        )
        human = self._post(2, _long("human"), self.old)
        featured = self._post(3, _long("featured"), self.old)
        sponsored = self._post(4, _long("sponsored"), self.old)
        self.conn.execute("INSERT INTO sponsorships(id, handle, tier) VALUES(1, 'sponsor', 'week')")
        ad_post = self._post(1, _long("adcopy"), self.old, ad_id=7)
        placed = self._post(1, _long("placed"), self.old)
        self.conn.execute("INSERT INTO ads(id, post_id, title) VALUES(7, ?, 'placement')", (placed,))
        lab = self._post(1, _long("labcard"), self.old)
        self.conn.execute("INSERT INTO career_cards(id, post_id, title) VALUES(5, ?, 'Steward')", (lab,))
        self.conn.execute("INSERT INTO problems(id, title, career_card_id) VALUES(1, 'citations', 5)")
        answered = self._post(1, _long("answered"), self.old)
        self._post(2, "a person answered", self.old, parent_id=answered)
        kept_text = self._post(1, _long("stay"), self.now)
        self.conn.commit()

        before = _files(self.path)
        with mock.patch.object(prune_posts, "delete_posts", side_effect=AssertionError("deleted")), \
             mock.patch.object(prune_posts, "_maybe_vacuum", side_effect=AssertionError("vacuum")):
            report = prune_posts.preview(self.path, now=self.now)
        after = _files(self.path)
        self.assertEqual(before, after)
        self.assertIsNone(after["-wal"])
        self.assertIsNone(after["-journal"])

        self.assertEqual(report["mode"], "dry-run")
        deleted = report["would_delete"]
        self.assertEqual(set(deleted["by_table"]), {"posts", "likes", "career_cards"})
        self.assertIn(extra, prune_posts.select_removals(self.conn, now=self.now)["remove_ids"])
        self.assertGreaterEqual(deleted["by_table"]["posts"], 2)
        self.assertGreaterEqual(deleted["by_table"]["likes"], 1)
        self.assertGreaterEqual(deleted["by_table"]["career_cards"], 1)
        self.assertEqual(deleted["total"], sum(deleted["by_table"].values()))
        self.assertGreater(report["size_bytes"], 0)
        self.assertLess(report["estimated_size_bytes"], report["size_bytes"])
        self.assertGreaterEqual(report["estimated_size_bytes"], 0)

        protected = report["protected"]
        categories = protected["by_category"]
        for name in (
            "non_simulated", "featured", "sponsorship", "ad_post",
            "ad_placement", "human_reply_parent", "problem_career_card",
        ):
            self.assertGreaterEqual(categories[name], 1, name)
        self.assertEqual(protected["total"], len(prune_posts.protected_post_ids(self.conn)))
        self.assertLessEqual(protected["total"], sum(categories.values()))
        for post_id in (human, featured, sponsored, ad_post, placed, lab, answered, kept_text, keep):
            self.assertNotIn(
                post_id,
                prune_posts.select_removals(self.conn, now=self.now)["remove_ids"],
            )

        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 12)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM career_cards").fetchone()[0], 2)
        finally:
            conn.close()


def _rows(conn, table):
    return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


class PreviewEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        try:
            os.remove(_DB)
        except OSError:
            pass

    def test_admin_preview_does_not_change_live_rows(self):
        missing = self.client.get("/api/admin/prune/preview")
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.get("/api/admin/prune/preview", headers={"Authorization": "Bearer not-a-key"})
        self.assertEqual(wrong.status_code, 401)

        c = app.db()
        try:
            bot_id = c.execute("SELECT id FROM bots WHERE simulated=1 LIMIT 1").fetchone()["id"]
            token = "preview-sentinel-" + uuid.uuid4().hex
            content = " ".join([token] * 12)
            cur = c.execute(
                "INSERT INTO posts(bot_id, content, created) VALUES(?,?,?)",
                (bot_id, content, time.time() - 30 * 86400),
            )
            sentinel = cur.lastrowid
            c.commit()
            before = {table: _rows(c, table) for table in (
                "posts", "likes", "career_cards", "bots", "humans", "problems", "ads",
            )}
        finally:
            c.close()

        try:
            ok = self.client.get("/api/admin/prune/preview", headers=ADMIN)
            self.assertEqual(ok.status_code, 200, ok.text)
            body = ok.json()
            self.assertEqual(body["mode"], "dry-run")
            self.assertGreaterEqual(body["would_delete"]["by_table"]["posts"], 1)
            self.assertEqual(
                body["would_delete"]["total"],
                sum(body["would_delete"]["by_table"].values()),
            )
            self.assertIn("non_simulated", body["protected"]["by_category"])
            self.assertIsInstance(body["protected"]["total"], int)
            self.assertGreaterEqual(body["size_bytes"], 0)
            self.assertGreaterEqual(body["estimated_size_bytes"], 0)
            self.assertLessEqual(body["estimated_size_bytes"], body["size_bytes"])
            self.assertNotIn("remove_ids", body)

            c = app.db()
            try:
                after = {table: _rows(c, table) for table in before}
                self.assertEqual(after, before)
                self.assertEqual(
                    c.execute("SELECT content FROM posts WHERE id=?", (sentinel,)).fetchone()["content"],
                    content,
                )
            finally:
                c.close()
        finally:
            c = app.db()
            try:
                c.execute("DELETE FROM posts WHERE id=?", (sentinel,))
                c.commit()
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
