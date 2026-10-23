"""POST /api/admin/prune/apply deletes only what the dry run predicted.

The job reuses scripts/prune_posts.py. It aborts when free disk is under 2.5x
the database file or when the recomputed counts drift past tolerance, and it
writes a sqlite backup that the public site does not serve.
"""

import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
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

import sim_budget

ADMIN = {"Authorization": "Bearer test-admin-d"}

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
CREATE TABLE content_reports(
    id INTEGER PRIMARY KEY,
    target_type TEXT,
    target_id INTEGER,
    reason TEXT
);
"""


def _long(seed, n=24):
    return " ".join(f"{seed}{i}" for i in range(n))


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class ToleranceAndDisk(unittest.TestCase):
    def test_tolerance_allows_small_drift_and_rejects_a_zero_baseline(self):
        self.assertTrue(prune_posts.within_tolerance(100, 100, 3))
        self.assertTrue(prune_posts.within_tolerance(103, 100, 3))
        self.assertFalse(prune_posts.within_tolerance(104, 100, 3))
        self.assertTrue(prune_posts.within_tolerance(0, 0, 3))
        self.assertFalse(prune_posts.within_tolerance(1, 0, 3))
        self.assertTrue(prune_posts.disk_headroom_ok(250, 100))
        self.assertFalse(prune_posts.disk_headroom_ok(249, 100))
        self.assertEqual(prune_posts.backup_directory("/data/botsocial.db"), "/data/backups")


class GuardedApply(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="prune-apply-")
        self.path = os.path.join(self.dir, "botsocial.db")
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
        shutil.rmtree(self.dir, ignore_errors=True)

    def _post(self, bot_id, content, created, parent_id=None, ad_id=None, post_type=None):
        cur = self.conn.execute(
            "INSERT INTO posts(bot_id, content, created, parent_id, ad_id, post_type) VALUES(?,?,?,?,?,?)",
            (bot_id, content, created, parent_id, ad_id, post_type),
        )
        return cur.lastrowid

    def _load(self, extra_quiet=0):
        dup = _long("dup")
        keep = self._post(1, dup, self.now - 3600)
        extra = self._post(1, dup, self.now - 10)
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(1, ?)", (keep,))
        self.conn.execute("INSERT INTO likes(bot_id, post_id) VALUES(3, ?)", (extra,))
        words = dup.split()
        near = " ".join(words[:-1] + ["edited"])
        self.assertTrue(sim_budget.texts_are_near(dup, near))
        near_id = self._post(1, near, self.now - 20)
        old_quiet = self._post(1, _long("quiet"), self.old)
        self.conn.execute(
            "INSERT INTO career_cards(id, post_id, title) VALUES(9, ?, 'Quiet card')",
            (old_quiet,),
        )
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
        self.conn.execute(
            "INSERT INTO content_reports(id, target_type, target_id, reason) VALUES(1, 'post', ?, 'spam')",
            (extra,),
        )
        for n in range(extra_quiet):
            self._post(1, _long(f"bulk{n}"), self.old)
        self.conn.commit()
        protected = {
            keep, human, featured, sponsored, lab_post, answered, human_reply,
            old_liked, recent_unique,
        }
        removed = {extra, near_id, old_quiet, sim_reply}
        return protected, removed

    def _install_post_fts(self, with_trigger):
        self.conn.execute(
            """CREATE VIEW search_posts_v AS
               SELECT id, COALESCE(content,'') AS content FROM posts"""
        )
        self.conn.execute(
            """CREATE VIRTUAL TABLE fts_posts USING fts5(
                   content, content='search_posts_v', content_rowid='id')"""
        )
        if with_trigger:
            self.conn.execute(
                """CREATE TRIGGER search_posts_ad AFTER DELETE ON posts BEGIN
                     INSERT INTO fts_posts(fts_posts, rowid, content)
                     VALUES ('delete', old.id, COALESCE(old.content,''));
                   END"""
            )
        self.conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")
        self.conn.commit()

    def _apply(self, **kwargs):
        preview = prune_posts.preview(self.path, now=self.now)
        args = {
            "expected_delete": preview["would_delete"]["total"],
            "expected_protected": preview["protected"]["total"],
            "vacuum": True,
            "now": self.now,
        }
        args.update(kwargs)
        result = prune_posts.run_guarded_apply(self.path, **args)
        return preview, result

    def test_apply_deletes_the_preview_and_keeps_protected_rows(self):
        protected, removed = self._load()
        try:
            self._install_post_fts(with_trigger=True)
        except sqlite3.OperationalError:
            self.skipTest("no fts5")
        before = sqlite3.connect(self.path)
        try:
            posts_before = _count(before, "posts")
            likes_before = _count(before, "likes")
            cards_before = _count(before, "career_cards")
        finally:
            before.close()

        preview, result = self._apply()
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["removed"], preview["would_delete"]["by_table"]["posts"])
        self.assertTrue(result["vacuumed"], result.get("vacuum_note"))
        self.assertIn("posts:triggers", result["search"])

        conn = sqlite3.connect(self.path)
        try:
            left = {row[0] for row in conn.execute("SELECT id FROM posts")}
            self.assertTrue(protected <= left, protected - left)
            self.assertTrue(removed.isdisjoint(left))
            self.assertEqual(posts_before - _count(conn, "posts"), preview["would_delete"]["by_table"]["posts"])
            self.assertEqual(likes_before - _count(conn, "likes"), preview["would_delete"]["by_table"]["likes"])
            self.assertEqual(
                cards_before - _count(conn, "career_cards"),
                preview["would_delete"]["by_table"]["career_cards"],
            )
            self.assertEqual(result["counts_after"]["posts"], _count(conn, "posts"))
            self.assertEqual(result["counts_after"]["likes"], _count(conn, "likes"))
            self.assertEqual(result["counts_after"]["career_cards"], _count(conn, "career_cards"))
            for table in ("bots", "humans", "ads", "problems", "sponsorships", "stripe_events", "content_reports"):
                self.assertEqual(result["counts_before"][table], result["counts_after"][table], table)
                self.assertGreaterEqual(result["counts_after"][table], 1, table)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fts_posts WHERE fts_posts MATCH 'quiet0'").fetchone()[0], 0)
            self.assertGreaterEqual(
                conn.execute("SELECT COUNT(*) FROM fts_posts WHERE fts_posts MATCH 'human0'").fetchone()[0],
                1,
            )
        finally:
            conn.close()

        self.assertGreater(result["size_bytes_before"], 0)
        self.assertGreater(result["size_bytes_after"], 0)
        self.assertGreater(result["backup_bytes"], 0)
        self.assertEqual(os.path.dirname(result["backup_path"]), prune_posts.backup_directory(self.path))
        self.assertRegex(os.path.basename(result["backup_path"]), r"^botsocial-\d{8}T\d{6}Z\.db$")
        backup = sqlite3.connect(result["backup_path"])
        try:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(_count(backup, "posts"), posts_before)
            self.assertEqual(result["backup_posts"], posts_before)
        finally:
            backup.close()

    def test_small_drift_still_applies(self):
        protected, removed = self._load(extra_quiet=40)
        preview = prune_posts.preview(self.path, now=self.now)
        actual = preview["would_delete"]["total"]
        self.assertGreaterEqual(actual, 40)
        result = prune_posts.run_guarded_apply(
            self.path,
            expected_delete=actual - 1,
            expected_protected=preview["protected"]["total"],
            tolerance_pct=3,
            vacuum=False,
            now=self.now,
        )
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertFalse(result["vacuumed"])
        self.assertEqual(result["vacuum_note"], "not requested")
        conn = sqlite3.connect(self.path)
        try:
            left = {row[0] for row in conn.execute("SELECT id FROM posts")}
        finally:
            conn.close()
        self.assertTrue(protected <= left)
        self.assertTrue(removed.isdisjoint(left))

    def test_mismatch_aborts_without_deleting(self):
        self._load()
        preview = prune_posts.preview(self.path, now=self.now)
        before = sqlite3.connect(self.path)
        try:
            posts_before = _count(before, "posts")
            likes_before = _count(before, "likes")
            cards_before = _count(before, "career_cards")
        finally:
            before.close()
        with mock.patch.object(prune_posts, "delete_posts", side_effect=AssertionError("deleted")):
            result = prune_posts.run_guarded_apply(
                self.path,
                expected_delete=0,
                expected_protected=preview["protected"]["total"],
                vacuum=True,
                now=self.now,
            )
        self.assertEqual(result["status"], "aborted_mismatch")
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["expected_delete"], 0)
        self.assertEqual(result["actual_delete"], preview["would_delete"]["total"])
        self.assertEqual(result["expected_protected"], preview["protected"]["total"])
        self.assertEqual(result["actual_protected"], preview["protected"]["total"])
        self.assertGreater(result["actual_delete"], 0)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(_count(conn, "posts"), posts_before)
            self.assertEqual(_count(conn, "likes"), likes_before)
            self.assertEqual(_count(conn, "career_cards"), cards_before)
        finally:
            conn.close()
        backup = sqlite3.connect(result["backup_path"])
        try:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(_count(backup, "posts"), posts_before)
        finally:
            backup.close()

    def test_low_disk_aborts_before_backup_or_delete(self):
        self._load()
        preview = prune_posts.preview(self.path, now=self.now)
        before = open(self.path, "rb").read()
        with mock.patch.object(prune_posts.shutil, "disk_usage") as usage, \
             mock.patch.object(prune_posts, "delete_posts", side_effect=AssertionError("deleted")):
            usage.return_value = mock.Mock(free=0)
            result = prune_posts.run_guarded_apply(
                self.path,
                expected_delete=preview["would_delete"]["total"],
                expected_protected=preview["protected"]["total"],
                vacuum=True,
                now=self.now,
            )
        self.assertEqual(result["status"], "aborted_disk", result.get("error"))
        self.assertEqual(result["disk"]["free_bytes"], 0)
        self.assertGreater(result["disk"]["need_bytes"], 0)
        self.assertFalse(disk_ok(result))
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "backups")))
        self.assertIsNone(result["backup_path"])
        self.assertEqual(open(self.path, "rb").read(), before)
        usage.assert_called()
        folder = usage.call_args[0][0]
        self.assertEqual(os.path.abspath(folder), os.path.abspath(self.dir))

    def test_rebuilds_fts_when_the_delete_trigger_is_missing(self):
        self._load()
        try:
            self._install_post_fts(with_trigger=False)
        except sqlite3.OperationalError:
            self.skipTest("no fts5")
        preview, result = self._apply(vacuum=False)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertIn("posts:rebuilt", result["search"])
        self.assertGreaterEqual(preview["would_delete"]["by_table"]["posts"], 1)
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM fts_posts WHERE fts_posts MATCH 'quiet0'").fetchone()[0],
                0,
            )
            self.assertGreaterEqual(
                conn.execute("SELECT COUNT(*) FROM fts_posts WHERE fts_posts MATCH 'human0'").fetchone()[0],
                1,
            )
        finally:
            conn.close()


def disk_ok(result):
    disk = result["disk"]
    return prune_posts.disk_headroom_ok(disk["free_bytes"], disk["size_bytes"])


class PruneApplyEndpoint(unittest.TestCase):
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

    def setUp(self):
        self._release = None
        deadline = time.time() + 3
        while app._prune_running and time.time() < deadline:
            if self._release:
                self._release.set()
            time.sleep(0.02)
        with app._prune_state_lock:
            if not app._prune_running:
                app._prune_job = None

    def tearDown(self):
        if self._release:
            self._release.set()
        deadline = time.time() + 3
        while app._prune_running and time.time() < deadline:
            time.sleep(0.02)
        with app._prune_state_lock:
            app._prune_running = False

    def test_backups_are_not_a_public_route(self):
        for route in app.app.routes:
            path = getattr(route, "path", "") or ""
            self.assertNotIn("backup", path.lower())
            mounted = getattr(getattr(route, "app", None), "directory", None)
            if mounted:
                directory = os.path.abspath(mounted)
                self.assertFalse(
                    directory == "/data" or directory.startswith("/data" + os.sep) or "backups" in directory,
                    directory,
                )
        missing = self.client.get("/data/backups/botsocial-20261023T000000Z.db")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.get("/backups/botsocial-20261023T000000Z.db").status_code, 404)

    def test_job_requires_admin_and_only_one_runs(self):
        self.assertEqual(self.client.post("/api/admin/prune/apply", json={"expected_delete": 1, "expected_protected": 1}).status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/admin/prune/apply",
                json={"expected_delete": 1, "expected_protected": 1},
                headers={"Authorization": "Bearer not-a-key"},
            ).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/admin/prune/job").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/prune/job", headers=ADMIN).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/admin/prune/apply",
                json={"expected_protected": 1},
                headers=ADMIN,
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/admin/prune/apply",
                json={"expected_delete": -1, "expected_protected": 0},
                headers=ADMIN,
            ).status_code,
            422,
        )

        started = threading.Event()
        release = threading.Event()
        self._release = release
        seen = {}

        def slow_run(db_path, **kwargs):
            seen["db_path"] = db_path
            seen["kwargs"] = kwargs
            on_step = kwargs.get("on_step")
            if on_step:
                on_step("disk_check")
            started.set()
            self.assertTrue(release.wait(5))
            if on_step:
                on_step("record")
            return {"status": "completed", "step": "record", "error": None, "removed": 0}

        mod = app._prune_posts_module()
        with mock.patch.object(mod, "run_guarded_apply", side_effect=slow_run):
            first = self.client.post(
                "/api/admin/prune/apply",
                json={"expected_delete": 4, "expected_protected": 9},
                headers=ADMIN,
            )
            self.assertEqual(first.status_code, 200, first.text)
            body = first.json()
            self.assertEqual(body["status"], "running")
            self.assertTrue(body["job_id"])
            self.assertTrue(started.wait(3))
            second = self.client.post(
                "/api/admin/prune/apply",
                json={"expected_delete": 4, "expected_protected": 9},
                headers=ADMIN,
            )
            self.assertEqual(second.status_code, 409)
            mid = self.client.get("/api/admin/prune/job", headers=ADMIN)
            self.assertEqual(mid.status_code, 200, mid.text)
            snapshot = mid.json()
            self.assertEqual(snapshot["job_id"], body["job_id"])
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(snapshot["actor"], "d")
            self.assertEqual(snapshot["step"], "disk_check")
            self.assertIn("disk_check", snapshot["timings"])
            self.assertEqual(snapshot["request"]["vacuum"], True)
            self.assertEqual(snapshot["request"]["tolerance_pct"], 3)
            release.set()
            deadline = time.time() + 3
            finished = snapshot
            while time.time() < deadline:
                finished = self.client.get("/api/admin/prune/job", headers=ADMIN).json()
                if finished["status"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["error"], None)
        self.assertFalse(app._prune_running)
        self.assertIsNotNone(finished["finished_at"])
        self.assertIn("record", finished["timings"])
        self.assertEqual(seen["db_path"], app.DB)
        self.assertEqual(seen["kwargs"]["expected_delete"], 4)
        self.assertEqual(seen["kwargs"]["expected_protected"], 9)
        self.assertEqual(seen["kwargs"]["tolerance_pct"], 3)
        self.assertTrue(seen["kwargs"]["vacuum"])

        c = app.db()
        try:
            self.assertGreaterEqual(c.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            started_row = c.execute(
                "SELECT actor_slug, ok, target FROM security_audit_log WHERE action='prune_apply' AND target=? ",
                (body["job_id"],),
            ).fetchone()
            self.assertIsNotNone(started_row)
            self.assertEqual(started_row["actor_slug"], "d")
            self.assertEqual(started_row["ok"], 1)
            blocked = c.execute(
                "SELECT actor_slug, ok, detail FROM security_audit_log WHERE action='prune_apply' AND ok=0 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(blocked["actor_slug"], "d")
            self.assertIn("already running", blocked["detail"])
            finish = c.execute(
                "SELECT actor_slug, ok FROM security_audit_log WHERE action='prune_apply_finish' AND target=?",
                (body["job_id"],),
            ).fetchone()
            self.assertEqual(finish["actor_slug"], "d")
            self.assertEqual(finish["ok"], 1)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
