"""POST /api/admin/prune/apply deletes only what the dry run predicted.

The job reuses scripts/prune_posts.py. It aborts when free disk is under 2.5x
the database file or when the recomputed counts drift past tolerance, and it
writes a sqlite backup that the public site does not serve.
"""

import asyncio
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
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


    def test_backup_copies_in_pages_without_loading_the_file(self):
        import inspect
        source = inspect.getsource(prune_posts._write_backup)
        self.assertIn("pages=BACKUP_PAGES", source)
        self.assertIn("sleep=BACKUP_SLEEP", source)
        self.assertNotIn(".read(", source)
        self.assertEqual(prune_posts.BACKUP_PAGES, 1024)
        self.assertGreater(prune_posts.BACKUP_SLEEP, 0)
        self.assertLess(prune_posts.BACKUP_SLEEP, 1)

    def test_partial_backup_is_removed_before_a_new_one(self):
        self._load()
        folder = prune_posts.backup_directory(self.path)
        os.makedirs(folder)
        partial = os.path.join(folder, "botsocial-20261023T000000Z.db.partial")
        kept = os.path.join(folder, "botsocial-20261022T000000Z.db")
        with open(partial, "wb") as handle:
            handle.write(b"partial-bytes")
        with open(kept, "wb") as handle:
            handle.write(b"finished")
        _preview, result = self._apply(vacuum=False)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertFalse(os.path.exists(partial))
        with open(kept, "rb") as handle:
            self.assertEqual(handle.read(), b"finished")
        self.assertTrue(os.path.isfile(result["backup_path"]))
        self.assertFalse(result["backup_path"].endswith(".partial"))

    def test_atomic_delete_rolls_back_and_a_rerun_finishes(self):
        self._load()
        preview = prune_posts.preview(self.path, now=self.now)

        def post_rows():
            conn = sqlite3.connect(self.path)
            try:
                return list(conn.execute("SELECT id, content FROM posts ORDER BY id"))
            finally:
                conn.close()

        before = post_rows()
        calls = {"n": 0}
        real = prune_posts._delete_chunk

        def flaky(conn, ids):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("killed mid-delete")
            return real(conn, ids)

        with mock.patch.object(prune_posts, "BATCH", 1), \
             mock.patch.object(prune_posts, "_delete_chunk", side_effect=flaky):
            failed = prune_posts.run_guarded_apply(
                self.path,
                expected_delete=preview["would_delete"]["total"],
                expected_protected=preview["protected"]["total"],
                vacuum=False,
                now=self.now,
            )
        self.assertEqual(failed["status"], "failed", failed.get("error"))
        self.assertIn("killed mid-delete", failed["error"])
        self.assertEqual(post_rows(), before)
        again = prune_posts.run_guarded_apply(
            self.path,
            expected_delete=preview["would_delete"]["total"],
            expected_protected=preview["protected"]["total"],
            vacuum=False,
            now=self.now,
        )
        self.assertEqual(again["status"], "completed", again.get("error"))
        self.assertGreater(again["removed"], 0)

    def test_repeated_delete_of_the_same_ids_is_safe(self):
        first = self._post(1, _long("gone-a"), self.old)
        second = self._post(1, _long("gone-b"), self.old)
        self.conn.commit()
        conn = sqlite3.connect(self.path)
        conn.isolation_level = None
        try:
            prune_posts.delete_posts(conn, [first, second], batch_size=1, atomic=False)
            again = prune_posts.delete_posts(conn, [first, second], batch_size=1, atomic=True)
            self.assertEqual(again, 2)
            left = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE id IN (?, ?)", (first, second)
            ).fetchone()[0]
            self.assertEqual(left, 0)
        finally:
            conn.close()

    def test_progress_callback_records_the_backup_before_delete(self):
        self._load()
        seen = []

        def on_update(snap):
            seen.append(dict(snap))

        preview = prune_posts.preview(self.path, now=self.now)
        result = prune_posts.run_guarded_apply(
            self.path,
            expected_delete=preview["would_delete"]["total"],
            expected_protected=preview["protected"]["total"],
            vacuum=False,
            now=self.now,
            on_update=on_update,
        )
        self.assertEqual(result["status"], "completed", result.get("error"))
        steps = [item["step"] for item in seen]
        self.assertIn("backup", steps)
        self.assertIn("delete", steps)
        delete = next(item for item in seen if item["step"] == "delete")
        self.assertTrue(delete["backup_path"])
        self.assertTrue(os.path.isfile(delete["backup_path"]))
        self.assertEqual(delete["actual_delete"], preview["would_delete"]["total"])

    def test_reconcile_marks_a_dead_pid_and_leaves_rows(self):
        self._load()
        with open(self.path, "rb") as handle:
            before = handle.read()
        folder = prune_posts.backup_directory(self.path)
        os.makedirs(folder)
        partial = os.path.join(folder, "botsocial-20261023T000000Z.db.partial")
        with open(partial, "wb") as handle:
            handle.write(b"partial")
        job_id = uuid.uuid4().hex
        path = prune_posts.job_file(self.path, job_id)
        prune_posts.write_job(path, {
            "id": job_id,
            "status": "running",
            "step": "preview",
            "actor": "d",
            "pid": _dead_pid(),
            "started_at": time.time() - 120,
            "finished_at": None,
            "timings": {},
            "error": None,
            "result": None,
            "request": {"expected_delete": 1, "expected_protected": 1},
            "backup_path": None,
            "numbers": {"expected_delete": 1},
        })
        changed = prune_posts.reconcile_jobs(self.path)
        self.assertEqual([job["id"] for job in changed], [job_id])
        state = prune_posts.read_job(path)
        self.assertEqual(state["status"], "interrupted")
        self.assertIn("database left unchanged", state["error"])
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertTrue(os.path.isfile(partial))
        live_id = uuid.uuid4().hex
        live_path = prune_posts.job_file(self.path, live_id)
        prune_posts.write_job(live_path, {
            "id": live_id,
            "status": "running",
            "step": "backup",
            "actor": "d",
            "pid": os.getpid(),
            "started_at": time.time(),
            "finished_at": None,
            "timings": {},
            "error": None,
            "request": {},
            "backup_path": None,
            "numbers": {},
        })
        prune_posts.reconcile_jobs(self.path)
        self.assertEqual(prune_posts.read_job(live_path)["status"], "running")
        # A job that has not written its pid yet is still spawning, not dead.
        fresh_id = uuid.uuid4().hex
        fresh_path = prune_posts.job_file(self.path, fresh_id)
        prune_posts.write_job(fresh_path, {
            "id": fresh_id,
            "status": "running",
            "step": "starting",
            "actor": "d",
            "pid": None,
            "started_at": time.time(),
            "finished_at": None,
            "timings": {},
            "error": None,
            "request": {},
            "backup_path": None,
            "numbers": {},
        })
        prune_posts.reconcile_jobs(self.path)
        self.assertEqual(prune_posts.read_job(fresh_path)["status"], "running")

    def test_job_process_persists_state_across_steps(self):
        protected, removed = self._load()
        preview = prune_posts.preview(self.path, now=self.now)
        job_id = uuid.uuid4().hex
        path = prune_posts.job_file(self.path, job_id)
        now = time.time()
        prune_posts.write_job(path, {
            "id": job_id,
            "status": "running",
            "step": "starting",
            "actor": "d",
            "pid": None,
            "started_at": now,
            "finished_at": None,
            "timings": {"starting": {"started_at": now, "finished_at": None, "seconds": None}},
            "error": None,
            "result": None,
            "request": {
                "expected_delete": preview["would_delete"]["total"],
                "expected_protected": preview["protected"]["total"],
                "tolerance_pct": 3,
                "vacuum": False,
            },
            "backup_path": None,
            "numbers": {},
        })
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "prune_job.py"), "--job", path, "--db", self.path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        state = prune_posts.read_job(path)
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertNotEqual(state["pid"], os.getpid())
        self.assertTrue(state["pid"])
        for step in ("disk_check", "backup", "verify_backup", "preview", "delete", "record"):
            self.assertIn(step, state["timings"], step)
        self.assertTrue(state["backup_path"])
        self.assertTrue(os.path.isfile(state["backup_path"]))
        self.assertEqual(state["numbers"]["removed"], preview["would_delete"]["by_table"]["posts"])
        self.assertEqual(state["numbers"]["actual_protected"], preview["protected"]["total"])
        self.assertIsNotNone(state["finished_at"])
        conn = sqlite3.connect(self.path)
        try:
            left = {row[0] for row in conn.execute("SELECT id FROM posts")}
        finally:
            conn.close()
        self.assertTrue(protected <= left)
        self.assertTrue(removed.isdisjoint(left))


def _dead_pid():
    pid = 1 << 22
    for _ in range(10000):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pass
        pid += 1
    raise AssertionError("no unused pid")


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

    def _clear_jobs(self):
        folder = prune_posts.prune_jobs_directory(app.DB)
        if not os.path.isdir(folder):
            return
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) or os.path.islink(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def setUp(self):
        self._release = None
        self._done = None
        self._clear_jobs()

    def tearDown(self):
        if self._release:
            self._release.set()
        if self._done:
            self._done.wait(3)
            time.sleep(0.05)
        self._clear_jobs()

    def test_backups_are_not_a_public_route(self):
        for route in app.app.routes:
            path = getattr(route, "path", "") or ""
            if path.startswith("/api/admin/backups"):
                continue
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
        self.assertEqual(self.client.get("/api/admin/backups").status_code, 401)

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
        done = threading.Event()
        self._release = release
        self._done = done
        seen = {}

        class FakeProc:
            pid = os.getpid()

            def wait(self):
                self.assert_wait = done.wait(5)
                return 0

        def fake_spawn(job_path, db_path):
            seen["db_path"] = db_path
            proc = FakeProc()

            def worker():
                job = prune_posts.read_job(job_path)
                seen["request"] = job["request"]
                seen["spawn"] = [sys.executable, "scripts/prune_job.py", "--job", job_path, "--db", db_path]
                job["pid"] = os.getpid()
                job["step"] = "disk_check"
                job["timings"]["disk_check"] = {"started_at": time.time(), "finished_at": None, "seconds": None}
                prune_posts.write_job(job_path, job)
                started.set()
                self.assertTrue(release.wait(5))
                job = prune_posts.read_job(job_path)
                job["status"] = "completed"
                job["step"] = "record"
                job["error"] = None
                job["finished_at"] = time.time()
                job["result"] = {"status": "completed", "step": "record", "error": None, "removed": 0}
                job["timings"]["record"] = {"started_at": time.time(), "finished_at": time.time(), "seconds": 0}
                prune_posts.write_job(job_path, job)
                app.audit_admin("d", "prune_apply_finish", target=job["id"], ok=1, detail="completed")
                done.set()

            threading.Thread(target=worker, daemon=True).start()
            return proc

        with mock.patch.object(app, "spawn_prune_process", side_effect=fake_spawn):
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
            self.assertEqual(snapshot["numbers"]["expected_delete"], 4)
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
        self.assertIsNotNone(finished["finished_at"])
        self.assertIn("record", finished["timings"])
        self.assertEqual(seen["db_path"], app.DB)
        self.assertEqual(seen["request"]["expected_delete"], 4)
        self.assertEqual(seen["request"]["expected_protected"], 9)
        self.assertEqual(seen["request"]["tolerance_pct"], 3)
        self.assertTrue(seen["request"]["vacuum"])
        self.assertIn("scripts/prune_job.py", seen["spawn"])

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

    def test_spawn_uses_a_separate_process(self):
        job_path = prune_posts.job_file(app.DB, uuid.uuid4().hex)
        with mock.patch.object(app.subprocess, "Popen") as popen:
            popen.return_value = mock.Mock(pid=321)
            proc = app.spawn_prune_process(job_path, app.DB)
        self.assertIs(proc, popen.return_value)
        args, kwargs = popen.call_args
        command = args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[-4].endswith("prune_job.py") or command[command.index("--job") - 1].endswith("prune_job.py"))
        self.assertIn("--job", command)
        self.assertEqual(command[command.index("--job") + 1], job_path)
        self.assertEqual(command[command.index("--db") + 1], app.DB)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_healthz_does_not_touch_the_database(self):
        with mock.patch.object(app, "db", side_effect=AssertionError("db")):
            res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"ok": True})
        with open(os.path.join(ROOT, "render.yaml"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\n    healthCheckPath: /healthz\n", text)
        self.assertNotIn("healthCheckPath: /\n", text)

    def test_job_get_marks_a_dead_process_interrupted(self):
        c = app.db()
        try:
            before = c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        finally:
            c.close()
        job_id = uuid.uuid4().hex
        path = prune_posts.job_file(app.DB, job_id)
        backup = "/data/backups/botsocial-20261023T000000Z.db"
        prune_posts.write_job(path, {
            "id": job_id,
            "status": "running",
            "step": "preview",
            "actor": "d",
            "pid": _dead_pid(),
            "started_at": time.time() - 120,
            "finished_at": None,
            "timings": {"preview": {"started_at": time.time() - 120, "finished_at": None, "seconds": None}},
            "error": None,
            "result": None,
            "request": {"expected_delete": 3, "expected_protected": 4, "tolerance_pct": 3, "vacuum": True},
            "backup_path": backup,
            "numbers": {"expected_delete": 3, "expected_protected": 4},
        })
        res = self.client.get("/api/admin/prune/job", headers=ADMIN)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["status"], "interrupted")
        self.assertEqual(body["step"], "preview")
        self.assertIn("database left unchanged", body["error"])
        self.assertEqual(body["backup_path"], backup)
        self.assertEqual(body["numbers"]["expected_delete"], 3)
        c = app.db()
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM posts").fetchone()[0], before)
            row = c.execute(
                "SELECT ok FROM security_audit_log WHERE action='prune_apply_finish' AND target=?",
                (job_id,),
            ).fetchone()
            self.assertEqual(row["ok"], 0)
        finally:
            c.close()

    def test_backup_list_and_delete_cannot_touch_the_database(self):
        folder = prune_posts.backup_directory(app.DB)
        os.makedirs(folder, exist_ok=True)
        name = "botsocial-20261023T010203Z.db"
        partial = "botsocial-20261023T010203Z-1.db.partial"
        link_name = "botsocial-20261023T010204Z.db"
        path = os.path.join(folder, name)
        partial_path = os.path.join(folder, partial)
        link = os.path.join(folder, link_name)
        try:
            with open(path, "wb") as handle:
                handle.write(b"abc")
            with open(partial_path, "wb") as handle:
                handle.write(b"zz")
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(app.DB, link)
            self.assertEqual(self.client.get("/api/admin/backups").status_code, 401)
            self.assertEqual(self.client.delete(f"/api/admin/backups/{name}").status_code, 401)
            listed = self.client.get("/api/admin/backups", headers=ADMIN)
            self.assertEqual(listed.status_code, 200, listed.text)
            by_name = {row["name"]: row for row in listed.json()["backups"]}
            self.assertEqual(by_name[name]["bytes"], 3)
            self.assertEqual(by_name[partial]["bytes"], 2)
            self.assertNotIn(link_name, by_name)
            self.assertNotIn(os.path.basename(app.DB), by_name)
            bad = self.client.delete("/api/admin/backups/botsocial.db", headers=ADMIN)
            self.assertEqual(bad.status_code, 400, bad.text)
            nope = self.client.delete("/api/admin/backups/nope.db", headers=ADMIN)
            self.assertEqual(nope.status_code, 400, nope.text)
            with self.assertRaises(ValueError):
                prune_posts.resolve_backup_name(app.DB, "../" + os.path.basename(app.DB))
            with self.assertRaises(ValueError):
                prune_posts.resolve_backup_name(app.DB, os.path.basename(app.DB))
            linked = self.client.delete(f"/api/admin/backups/{link_name}", headers=ADMIN)
            self.assertEqual(linked.status_code, 400, linked.text)
            self.assertTrue(os.path.isfile(app.DB))
            self.assertTrue(os.path.islink(link))
            missing = self.client.delete(
                "/api/admin/backups/botsocial-20261023T010205Z.db", headers=ADMIN,
            )
            self.assertEqual(missing.status_code, 404)
            deleted = self.client.delete(f"/api/admin/backups/{partial}", headers=ADMIN)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(deleted.json()["bytes"], 2)
            self.assertFalse(os.path.exists(partial_path))
            c = app.db()
            try:
                row = c.execute(
                    "SELECT actor_slug, ok, target FROM security_audit_log WHERE action='backup_delete' AND target=? ORDER BY id DESC LIMIT 1",
                    (partial,),
                ).fetchone()
                self.assertEqual(row["actor_slug"], "d")
                self.assertEqual(row["ok"], 1)
                self.assertEqual(row["target"], partial)
            finally:
                c.close()
        finally:
            for leftover in (path, partial_path, link):
                if not os.path.lexists(leftover):
                    continue
                if os.path.islink(leftover) or os.path.realpath(leftover) != os.path.realpath(app.DB):
                    os.remove(leftover)

    def test_render_startup_opens_before_init_finishes(self):
        started = threading.Event()
        release = threading.Event()

        def slow_init():
            started.set()
            if not release.wait(2):
                raise TimeoutError("init blocked the port")

        async def idle():
            await asyncio.Event().wait()

        async def exercise():
            with mock.patch.object(app, "init", slow_init), \
                 mock.patch.object(app, "_warmup_sim_steps", lambda: None), \
                 mock.patch.object(app, "loop", idle):
                async with app.lifespan(app.app):
                    for _ in range(40):
                        if started.is_set():
                            break
                        await asyncio.sleep(0.05)
                    self.assertTrue(started.is_set())
                    release.set()
                    await asyncio.sleep(0.2)

        previous = os.environ.get("RENDER")
        os.environ["RENDER"] = "true"
        try:
            asyncio.run(exercise())
        finally:
            release.set()
            if previous is None:
                os.environ.pop("RENDER", None)
            else:
                os.environ["RENDER"] = previous


if __name__ == "__main__":
    unittest.main()
