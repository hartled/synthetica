"""prune_posts.py defaults to a dry run and never selects protected rows."""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location(
    "prune_posts", os.path.join(ROOT, "scripts", "prune_posts.py")
)
prune_posts = importlib.util.module_from_spec(_spec)
sys.modules["prune_posts"] = prune_posts
_spec.loader.exec_module(prune_posts)

import sim_budget


SCHEMA = """
CREATE TABLE bots(
    id INTEGER PRIMARY KEY,
    handle TEXT,
    simulated INTEGER DEFAULT 0,
    featured_until REAL
);
CREATE TABLE humans(id INTEGER PRIMARY KEY, email TEXT);
CREATE TABLE posts(
    id INTEGER PRIMARY KEY,
    bot_id INTEGER,
    parent_id INTEGER REFERENCES posts(id),
    content TEXT,
    created REAL,
    ad_id INTEGER,
    post_type TEXT
);
CREATE TABLE likes(bot_id INTEGER, post_id INTEGER, PRIMARY KEY(bot_id, post_id));
CREATE TABLE career_cards(id INTEGER PRIMARY KEY, post_id INTEGER, title TEXT);
CREATE TABLE ads(id INTEGER PRIMARY KEY, post_id INTEGER, title TEXT);
CREATE TABLE problems(id INTEGER PRIMARY KEY, title TEXT, career_card_id INTEGER);
CREATE TABLE sponsorships(id INTEGER PRIMARY KEY, handle TEXT, tier TEXT);
CREATE TABLE stripe_events(id TEXT PRIMARY KEY, event_type TEXT, created REAL);
"""


def _long(seed, n=24):
    return " ".join(f"{seed}{i}" for i in range(n))


class PruneTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.now = time.time()
        self.old = self.now - 30 * 86400
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(1, 'novacareers', 1)")
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(2, 'realbot', 0)")
        self.conn.execute(
            "INSERT INTO bots(id, handle, simulated, featured_until) VALUES(3, 'paidbot', 1, ?)",
            (self.now + 86400,),
        )
        self.conn.execute("INSERT INTO bots(id, handle, simulated) VALUES(4, 'sponsorbot', 0)")
        self.conn.execute("INSERT INTO humans(id, email) VALUES(1, 'reader@example.com')")
        self.conn.execute(
            "INSERT INTO stripe_events(id, event_type, created) VALUES('evt_1', 'checkout.session.completed', ?)",
            (self.now,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.remove(self.path)
        except OSError:
            pass

    def _post(self, bot_id, content, created, parent_id=None, ad_id=None, post_type=None):
        cur = self.conn.execute(
            "INSERT INTO posts(bot_id, content, created, parent_id, ad_id, post_type) VALUES(?,?,?,?,?,?)",
            (bot_id, content, created, parent_id, ad_id, post_type),
        )
        return cur.lastrowid

    def _plan(self):
        self.conn.commit()
        report = prune_posts.select_removals(self.conn, now=self.now, min_age_days=14)
        return report

    def test_dry_run_deletes_nothing_and_apply_only_removes_selected_rows(self):
        dup = _long("dup")
        keep = self._post(1, dup, self.now - 3600)
        extra = self._post(1, dup, self.now - 10)
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(1, ?)", (keep,))
        words = dup.split()
        near = " ".join(words[:-1] + ["edited"])
        self.assertTrue(sim_budget.texts_are_near(dup, near))
        near_id = self._post(1, near, self.now - 20)
        old_quiet = self._post(1, _long("quiet"), self.old)
        old_liked = self._post(1, _long("liked"), self.old)
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(2, ?)", (old_liked,))
        recent_unique = self._post(1, _long("fresh"), self.now - 86400)
        human = self._post(2, _long("human"), self.old)
        featured = self._post(3, _long("featured"), self.old)
        sponsored = self._post(4, "sponsored copy of a post", self.old, ad_id=7)
        self.conn.execute(
            "INSERT INTO ads(id, post_id, title) VALUES(7, ?, 'placement')",
            (sponsored,),
        )
        lab_post = self._post(1, _long("labcard"), self.old, post_type="career_card")
        self.conn.execute(
            "INSERT INTO career_cards(id, post_id, title) VALUES(5, ?, 'Citation Steward')",
            (lab_post,),
        )
        self.conn.execute(
            "INSERT INTO problems(id, title, career_card_id) VALUES(1, 'Models invent citations', 5)"
        )
        answered = self._post(1, _long("answered"), self.old)
        human_reply = self._post(2, "a person answered this", self.old, parent_id=answered)
        sim_reply = self._post(1, _long("simreply"), self.old, parent_id=recent_unique)
        self.conn.execute(
            "INSERT INTO sponsorships(id, handle, tier) VALUES(1, 'sponsorbot', 'week')"
        )
        self.conn.commit()

        protected = {
            keep, human, featured, sponsored, lab_post, answered, human_reply,
            old_liked, recent_unique,
        }
        dry = prune_posts.execute(self.path, apply=False, now=self.now)
        self.assertEqual(dry["mode"], "dry-run")
        self.assertEqual(dry["removed"], 0)
        self.assertGreater(dry["would_remove"], 0)
        self.assertIn("posts", dry["tables"])
        self.assertIn("stripe_events", dry["tables"])
        self.assertGreaterEqual(dry["size_bytes"], 0)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 13)
        finally:
            conn.close()

        peek = sqlite3.connect(self.path)
        try:
            plan = prune_posts.select_removals(peek, now=self.now)
            selected = set(plan["remove_ids"])
        finally:
            peek.close()
        self.assertTrue(selected.isdisjoint(protected), selected & protected)
        self.assertIn(extra, selected)
        self.assertIn(near_id, selected)
        self.assertIn(old_quiet, selected)
        self.assertIn(sim_reply, selected)
        self.assertNotIn(keep, selected)
        self.assertEqual(plan["by_reason"]["near_duplicate"] >= 2, True)
        self.assertGreaterEqual(plan["by_reason"]["low_engagement"], 1)

        # Default CLI is a dry run, and --dry-run wins over --apply.
        self.assertEqual(prune_posts.main(["--db", self.path, "--dry-run", "--apply"]), 0)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 13)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stripe_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM humans").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0], 1)
        finally:
            conn.close()

        with mock.patch("prune_posts.shutil.disk_usage") as usage:
            usage.return_value = mock.Mock(free=1)
            starved = prune_posts.execute(self.path, apply=True, vacuum=True, now=self.now, batch_size=2)
        self.assertGreater(starved["removed"], 0)
        self.assertFalse(starved["vacuumed"])
        self.assertIn("skipped VACUUM", starved["vacuum_note"])

        conn = sqlite3.connect(self.path)
        try:
            left = {row[0] for row in conn.execute("SELECT id FROM posts")}
            self.assertTrue(protected <= left, protected - left)
            self.assertNotIn(extra, left)
            self.assertNotIn(near_id, left)
            self.assertNotIn(old_quiet, left)
            self.assertNotIn(sim_reply, left)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM career_cards").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stripe_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM humans").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT post_id FROM career_cards").fetchone()[0],
                lab_post,
            )
        finally:
            conn.close()

        vacuumed = prune_posts.execute(self.path, apply=True, vacuum=True, now=self.now)
        self.assertEqual(vacuumed["removed"], 0)
        self.assertTrue(vacuumed["vacuumed"])


if __name__ == "__main__":
    unittest.main()
