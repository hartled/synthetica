"""Daily simulator budget: one cap across connections, workers, and restarts.

Bearer posts do not consult the cap. The career-tick mix test leaves the cap
off so it can still measure roll shares.
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

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
import sim_budget
from fastapi.testclient import TestClient
from personas_extra import CAREER_POSTS, career_post_text

DAY = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
ADMIN = {"Authorization": "Bearer test-admin-d"}


def _connect(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _posts_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY,
            bot_id INTEGER,
            parent_id INTEGER,
            content TEXT,
            created REAL,
            post_type TEXT)"""
    )


class BudgetEnv(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "SIM_DAILY_POST_BUDGET",
                "SIM_CAREER_SHARE_CAP",
                "SIM_DEDUPE_DAYS",
                "SIM_DAILY_LIKE_BUDGET",
                "SIM_DAILY_FOLLOW_BUDGET",
                "SIM_NEAR_DUP_JACCARD",
            )
        }
        os.environ["SIM_DAILY_POST_BUDGET"] = "30"
        os.environ["SIM_CAREER_SHARE_CAP"] = "0.4"
        os.environ["SIM_DEDUPE_DAYS"] = "14"
        os.environ["SIM_DAILY_LIKE_BUDGET"] = "120"
        os.environ["SIM_DAILY_FOLLOW_BUDGET"] = "40"
        os.environ["SIM_NEAR_DUP_JACCARD"] = "0.90"
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        sim_budget.clear_cache()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sim_budget.clear_cache(self.path)
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_distinct_career_cards_are_not_near_duplicates(self):
        texts = [career_post_text(card) for card in CAREER_POSTS]
        self.assertGreaterEqual(len(texts), 2)
        for left in range(len(texts)):
            for right in range(left + 1, len(texts)):
                self.assertFalse(
                    sim_budget.texts_are_near(texts[left], texts[right]),
                    (CAREER_POSTS[left]["title"], CAREER_POSTS[right]["title"]),
                )

    def test_cap_spread_restart_and_utc_day(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "3"
        interval = 86400 / 3
        conn = _connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(sim_budget.reserve_post(conn, DAY))
            self.assertFalse(sim_budget.reserve_post(conn, DAY + 1))
            self.assertTrue(sim_budget.reserve_post(conn, DAY + interval))
            self.assertTrue(sim_budget.reserve_post(conn, DAY + 2 * interval))
            # 3 * interval is exactly 86400s, which is the next UTC day.
            still_today = DAY + 2 * interval + 10
            self.assertFalse(sim_budget.reserve_post(conn, still_today))
            self.assertEqual(sim_budget.snapshot(conn, still_today)["posts"], 3)
            conn.execute("COMMIT")
        finally:
            conn.close()

        reopened = _connect(self.path)
        try:
            reopened.execute("BEGIN IMMEDIATE")
            self.assertFalse(sim_budget.reserve_post(reopened, DAY + 80000))
            self.assertEqual(sim_budget.snapshot(reopened, DAY + 80000)["posts"], 3)
            self.assertTrue(sim_budget.reserve_post(reopened, DAY + 86400))
            self.assertEqual(sim_budget.snapshot(reopened, DAY + 86400)["posts"], 1)
            reopened.execute("COMMIT")
        finally:
            reopened.close()

    def test_workers_share_one_budget(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "1"
        setup = _connect(self.path)
        sim_budget.ensure(setup)
        setup.close()
        results = []
        errors = []

        def work():
            conn = _connect(self.path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                ok = sim_budget.reserve_post(conn, DAY)
                conn.execute("COMMIT")
                results.append(ok)
            except Exception as exc:
                errors.append(exc)
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            finally:
                conn.close()

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for ok in results if ok), 1)
        conn = _connect(self.path)
        try:
            self.assertEqual(sim_budget.snapshot(conn, DAY)["posts"], 1)
        finally:
            conn.close()

    def test_career_share_stays_inside_the_cap(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "10"
        os.environ["SIM_CAREER_SHARE_CAP"] = "0.4"
        interval = 86400 / 10
        conn = _connect(self.path)
        now = DAY
        posts = career = 0
        try:
            for _ in range(40):
                conn.execute("BEGIN IMMEDIATE")
                if sim_budget.reserve_post(conn, now, career=True):
                    career += 1
                    posts += 1
                    now += interval
                elif sim_budget.reserve_post(conn, now, career=False):
                    posts += 1
                    now += interval
                conn.execute("COMMIT")
                if posts >= 10:
                    break
        finally:
            conn.close()
        self.assertEqual(posts, 10)
        self.assertGreater(career, 0)
        self.assertLessEqual(career, 4)
        self.assertLessEqual(career / posts, 0.4 + 1e-9)

    def test_near_duplicate_does_not_consume_a_slot(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "3"
        words = [f"token{i}" for i in range(24)]
        original = " ".join(words)
        shifted = " ".join(words[:-1] + ["tokenx"])
        other = " ".join(f"different{i}" for i in range(24))
        self.assertTrue(sim_budget.texts_are_near(original, shifted))
        self.assertFalse(sim_budget.texts_are_near(original, other))
        conn = _connect(self.path)
        try:
            _posts_table(conn)
            conn.execute("BEGIN IMMEDIATE")
            self.assertEqual(
                app._publish_sim_post(conn, 1, original, DAY),
                "ok",
            )
            self.assertEqual(
                app._publish_sim_post(conn, 1, "  " + original.upper() + "  ", DAY + 86400 / 3),
                "dup",
            )
            self.assertEqual(sim_budget.snapshot(conn, DAY)["posts"], 1)
            self.assertEqual(
                app._publish_sim_post(conn, 1, shifted, DAY + 86400 / 3),
                "dup",
            )
            self.assertEqual(
                app._publish_sim_post(conn, 1, other, DAY + 86400 / 3),
                "ok",
            )
            self.assertEqual(sim_budget.snapshot(conn, DAY)["posts"], 2)
            self.assertFalse(sim_budget.is_near_duplicate(conn, other, DAY + 15 * 86400))
            conn.execute("COMMIT")
        finally:
            conn.close()

    def test_like_and_follow_caps_stop_new_rows(self):
        os.environ["SIM_DAILY_LIKE_BUDGET"] = "2"
        os.environ["SIM_DAILY_FOLLOW_BUDGET"] = "2"
        conn = _connect(self.path)
        try:
            conn.execute("CREATE TABLE likes(bot_id INTEGER, post_id INTEGER, PRIMARY KEY(bot_id, post_id))")
            conn.execute(
                "CREATE TABLE follows(follower_id INTEGER, followee_id INTEGER, PRIMARY KEY(follower_id, followee_id))"
            )
            conn.execute("BEGIN IMMEDIATE")
            with mock.patch("app.time.time", return_value=DAY):
                app._sim_like(conn, 1, 10)
                app._sim_like(conn, 1, 11)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 1)
            with mock.patch("app.time.time", return_value=DAY + 86400 / 2):
                app._sim_like(conn, 1, 11)
                app._sim_like(conn, 1, 12)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 2)
            with mock.patch("app.time.time", return_value=DAY + 86400 / 2 + 10):
                app._sim_like(conn, 1, 13)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 2)
            with mock.patch("app.time.time", return_value=DAY):
                app._sim_follow(conn, 1, 2)
                app._sim_follow(conn, 1, 2)
                app._sim_follow(conn, 1, 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0], 1)
            conn.execute("COMMIT")
        finally:
            conn.close()

    def test_suggest_sleep_waits_for_the_next_slot(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "30"
        os.environ["SIM_DAILY_LIKE_BUDGET"] = "0"
        os.environ["SIM_DAILY_FOLLOW_BUDGET"] = "0"
        conn = _connect(self.path)
        try:
            self.assertEqual(sim_budget.suggest_sleep(conn, DAY), 0)
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(sim_budget.reserve_post(conn, DAY))
            conn.execute("COMMIT")
            self.assertEqual(sim_budget.suggest_sleep(conn, DAY), 900)
            self.assertEqual(sim_budget.suggest_sleep(conn, DAY + 86400), 0)
        finally:
            conn.close()


class SimulatorAndBearerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init()
        app.bootstrap_named_admin_keys("test-admin-d", "test-admin-ceo")
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self._saved = os.environ.get("SIM_DAILY_POST_BUDGET")
        os.environ["SIM_DAILY_POST_BUDGET"] = "30"
        app._hits.clear()
        sim_budget.clear_cache()
        conn = app.db()
        conn.isolation_level = None
        try:
            sim_budget.ensure(conn)
            conn.execute("DELETE FROM sim_quota")
        finally:
            conn.close()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SIM_DAILY_POST_BUDGET", None)
        else:
            os.environ["SIM_DAILY_POST_BUDGET"] = self._saved
        sim_budget.clear_cache()

    def _sim_posts(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM posts p JOIN bots b ON b.id=p.bot_id WHERE b.simulated=1"
            ).fetchone()[0]
        finally:
            conn.close()

    def test_step_stops_posting_until_a_new_connection_still_sees_the_cap(self):
        conn = app.db()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(sim_budget.reserve_post(conn, time.time()))
            conn.execute("COMMIT")
        finally:
            conn.close()
        before = self._sim_posts()
        for _ in range(15):
            app.step()
        self.assertEqual(self._sim_posts(), before)
        again = app.db()
        try:
            self.assertGreaterEqual(sim_budget.snapshot(again, time.time())["posts"], 1)
        finally:
            again.close()

    def test_bearer_posts_ignore_a_full_sim_budget(self):
        os.environ["SIM_DAILY_POST_BUDGET"] = "1"
        conn = app.db()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(sim_budget.reserve_post(conn, time.time(), career=False))
            conn.execute("COMMIT")
        finally:
            conn.close()
        handle = "b" + uuid.uuid4().hex[:8]
        with mock.patch("sim_budget.reserve_post", side_effect=AssertionError("sim cap must not run")):
            created = self.client.post(
                "/api/bots",
                json={"name": "Budget Peer", "handle": handle, "category": "other"},
            )
            self.assertEqual(created.status_code, 201, created.text)
            headers = {"Authorization": "Bearer " + created.json()["api_key"]}
            for index in range(4):
                posted = self.client.post(
                    "/api/posts",
                    json={"content": f"human-operated bot post {index} {handle}"},
                    headers=headers,
                )
                self.assertEqual(posted.status_code, 201, posted.text)
            card = self.client.post(
                "/api/career-cards",
                json={
                    "title": "Night Auditor",
                    "what_it_does": "Reads the day's ledger before the building sleeps.",
                    "who_hires": "Hotels and campus desks",
                    "skills_needed": "Arithmetic and a calm voice",
                    "why_now": "The night shift still needs a person.",
                },
                headers=headers,
            )
            self.assertEqual(card.status_code, 201, card.text)

    def test_admin_db_usage_requires_an_admin(self):
        missing = self.client.get("/api/admin/db")
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.get("/api/admin/db", headers={"Authorization": "Bearer not-a-key"})
        self.assertEqual(wrong.status_code, 401)
        ok = self.client.get("/api/admin/db", headers=ADMIN)
        self.assertEqual(ok.status_code, 200, ok.text)
        body = ok.json()
        self.assertIsInstance(body["size_bytes"], int)
        self.assertGreaterEqual(body["size_bytes"], 0)
        self.assertIn("posts", body["tables"])
        self.assertIsInstance(body["tables"]["posts"], int)
        self.assertIn("bots", body["tables"])
        self.assertIn("sim_budget", body)
        self.assertTrue(body["sim_budget"]["enforced"])
        self.assertEqual(body["sim_budget"]["post_budget"], 30)


if __name__ == "__main__":
    unittest.main()
