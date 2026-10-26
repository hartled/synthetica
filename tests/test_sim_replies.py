"""Built-in simulated bots must not template-reply to each other.

Posts, likes, and follows still run. Real API bots (simulated=0), including
supportive peers such as mentor_mae, are not driven by the simulator.
"""
import os
import tempfile
import time
import unittest
import uuid
from unittest import mock

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

from fastapi.testclient import TestClient

import app


def _clear_activity(c):
    c.execute("DELETE FROM likes")
    c.execute("DELETE FROM follows")
    c.execute("DELETE FROM posts WHERE parent_id IS NOT NULL AND ad_id IS NULL")
    c.execute(
        """DELETE FROM posts WHERE parent_id IS NULL AND ad_id IS NULL
           AND id NOT IN (SELECT post_id FROM ads WHERE post_id IS NOT NULL)"""
    )
    c.commit()


def _insert_post(c, bot_id, content, parent_id=None):
    cur = c.execute(
        "INSERT INTO posts(bot_id,parent_id,content,created) VALUES(?,?,?,?)",
        (bot_id, parent_id, content, time.time()),
    )
    return cur.lastrowid


def _sim_on_sim(c):
    return c.execute(
        """SELECT r.id, rb.handle replier, pb.handle parent_author
           FROM posts r
           JOIN posts p ON p.id = r.parent_id
           JOIN bots rb ON rb.id = r.bot_id
           JOIN bots pb ON pb.id = p.bot_id
           WHERE rb.simulated=1 AND pb.simulated=1"""
    ).fetchall()


def _counts(c, simulated):
    return {
        row["id"]: row["n"]
        for row in c.execute(
            """SELECT b.id id, (SELECT COUNT(*) FROM posts p WHERE p.bot_id=b.id) n
               FROM bots b WHERE b.simulated=?""",
            (simulated,),
        )
    }


def _ensure_real(c, handle, name):
    row = c.execute("SELECT id FROM bots WHERE handle=?", (handle,)).fetchone()
    if row:
        c.execute("UPDATE bots SET simulated=0, banned=0 WHERE id=?", (row["id"],))
        return row["id"]
    cur = c.execute(
        "INSERT INTO bots(name,handle,persona,avatar,simulated,created,category) VALUES(?,?,?,?,0,?,?)",
        (name, handle, "Real API peer", "🤖", time.time(), "wellbeing"),
    )
    return cur.lastrowid


class SimReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These cases call step() in a tight loop and expect the unpaced rolls.
        cls._prev_budget = os.environ.get("SIM_DAILY_POST_BUDGET")
        os.environ["SIM_DAILY_POST_BUDGET"] = "off"
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        try:
            os.remove(_DB)
        except OSError:
            pass
        if cls._prev_budget is None:
            os.environ.pop("SIM_DAILY_POST_BUDGET", None)
        else:
            os.environ["SIM_DAILY_POST_BUDGET"] = cls._prev_budget

    def setUp(self):
        app._hits.clear()
        c = app.db()
        try:
            _clear_activity(c)
        finally:
            c.close()

    def test_simulated_bots_do_not_template_reply_to_each_other(self):
        c = app.db()
        try:
            sim_ids = [r["id"] for r in c.execute(
                "SELECT id FROM bots WHERE simulated=1 AND banned=0 ORDER BY id LIMIT 4")]
            self.assertGreaterEqual(len(sim_ids), 2)
            for i, bid in enumerate(sim_ids):
                _insert_post(c, bid, f"simulated original {i}")
            c.commit()
            with mock.patch("app.random.random", return_value=0.0):
                for _ in range(40):
                    app._step_before_ads()
            self.assertEqual(_sim_on_sim(c), [])
            n = c.execute(
                "SELECT COUNT(*) n FROM posts WHERE parent_id IS NOT NULL AND ad_id IS NULL"
            ).fetchone()["n"]
            self.assertEqual(n, 0)
        finally:
            c.close()

    def test_posts_likes_and_follows_still_happen(self):
        c = app.db()
        try:
            sim_ids = [r["id"] for r in c.execute(
                "SELECT id FROM bots WHERE simulated=1 AND banned=0 ORDER BY id LIMIT 4")]
            for i, bid in enumerate(sim_ids):
                _insert_post(c, bid, f"feed post {i}")
            c.commit()
            real_before = _counts(c, 0)
            top_before = c.execute(
                "SELECT COUNT(*) n FROM posts WHERE parent_id IS NULL AND ad_id IS NULL"
            ).fetchone()["n"]
            actions = []
            for inner in ([0.1] * 8) + ([0.55] * 12) + ([0.72] * 20) + ([0.9] * 8):
                actions.append(0.5)  # outer step skips ad replies
                actions.append(inner)
            seq = iter(actions)
            with mock.patch("app.random.random", side_effect=lambda: next(seq)):
                for _ in range(len(actions) // 2):
                    app.step()
            self.assertEqual(_sim_on_sim(c), [])
            likes = c.execute("SELECT COUNT(*) n FROM likes").fetchone()["n"]
            follows = c.execute("SELECT COUNT(*) n FROM follows").fetchone()["n"]
            top_after = c.execute(
                "SELECT COUNT(*) n FROM posts WHERE parent_id IS NULL AND ad_id IS NULL"
            ).fetchone()["n"]
            self.assertGreater(likes, 0)
            self.assertGreater(follows, 0)
            self.assertGreater(top_after, top_before)
            self.assertEqual(_counts(c, 0), real_before)
        finally:
            c.close()

    def test_replies_to_real_authors_only(self):
        c = app.db()
        try:
            sim_id = c.execute(
                "SELECT id FROM bots WHERE simulated=1 AND banned=0 ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            mae = _ensure_real(c, "mentor_mae", "Mentor Mae")
            forge = _ensure_real(c, "forge_mentor", "Forge Mentor")
            self.assertEqual(c.execute("SELECT simulated FROM bots WHERE id=?", (mae,)).fetchone()["simulated"], 0)
            self.assertEqual(c.execute("SELECT simulated FROM bots WHERE id=?", (forge,)).fetchone()["simulated"], 0)
            real_post = _insert_post(c, mae, "a real peer wrote this")
            sim_root = _insert_post(c, sim_id, "a simulated bot wrote this")
            _insert_post(c, forge, "real reply under a simulated root", sim_root)
            _insert_post(c, sim_id, "simulated reply under a real root", real_post)
            c.commit()
            real_before = _counts(c, 0)
            with mock.patch("app.random.random", return_value=0.1):
                for _ in range(25):
                    app._step_before_ads()
            self.assertEqual(_sim_on_sim(c), [])
            replies = c.execute(
                """SELECT r.bot_id, r.parent_id, r.content, rb.simulated author_sim, pb.simulated parent_sim
                   FROM posts r
                   JOIN bots rb ON rb.id = r.bot_id
                   JOIN posts p ON p.id = r.parent_id
                   JOIN bots pb ON pb.id = p.bot_id
                   WHERE r.parent_id IS NOT NULL AND r.ad_id IS NULL
                     AND r.content NOT IN ('real reply under a simulated root', 'simulated reply under a real root')"""
            ).fetchall()
            self.assertGreater(len(replies), 0)
            for row in replies:
                self.assertEqual(row["parent_id"], real_post)
                self.assertEqual(row["parent_sim"], 0)
                self.assertEqual(row["author_sim"], 1)
                self.assertTrue(row["content"])
            self.assertEqual(_counts(c, 0), real_before)
        finally:
            c.close()

    def test_api_bots_still_post_their_own_replies(self):
        handle = "api" + uuid.uuid4().hex[:8]
        created = self.client.post("/api/bots", json={"name": "Api Peer", "handle": handle, "category": "wellbeing"})
        self.assertEqual(created.status_code, 201, created.text)
        key = created.json()["api_key"]
        headers = {"Authorization": "Bearer " + key}
        top = self.client.post("/api/posts", json={"content": "hello from a real bot"}, headers=headers)
        self.assertEqual(top.status_code, 201, top.text)
        reply = self.client.post(
            "/api/posts",
            json={"content": "a real reply, not a template", "parent_id": top.json()["id"]},
            headers=headers,
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        self.assertEqual(reply.json()["content"], "a real reply, not a template")
        c = app.db()
        try:
            bot = c.execute("SELECT id, simulated, house FROM bots WHERE handle=?", (handle,)).fetchone()
            self.assertEqual(bot["simulated"], 0)
            self.assertEqual(bot["house"], 0)
            before = _counts(c, 0)
            with mock.patch("app.random.random", return_value=0.0):
                for _ in range(15):
                    app._step_before_ads()
            self.assertEqual(_counts(c, 0), before)
            self.assertEqual(_sim_on_sim(c), [])
            stored = c.execute("SELECT content FROM posts WHERE id=?", (reply.json()["id"],)).fetchone()["content"]
            self.assertEqual(stored, "a real reply, not a template")
            self.assertFalse(created.json()["house"])
        finally:
            c.close()

    def test_mentor_handles_are_house_but_not_simulated(self):
        from bots.mentor_runner import MENTORS
        self.assertEqual(app.HOUSE_API_HANDLES, MENTORS)
        c = app.db()
        try:
            ids = {handle: _ensure_real(c, handle, handle) for handle in MENTORS}
            _ensure_real(c, "not_a_house_bot", "Stranger")
            c.commit()
        finally:
            c.close()
        app.migrate()
        app.migrate()
        c = app.db()
        try:
            for handle in MENTORS:
                row = c.execute(
                    "SELECT simulated, COALESCE(house,0) house FROM bots WHERE handle=?",
                    (handle,),
                ).fetchone()
                self.assertEqual(row["simulated"], 0, handle)
                self.assertEqual(row["house"], 1, handle)
            stranger = c.execute(
                "SELECT simulated, COALESCE(house,0) house FROM bots WHERE handle=?",
                ("not_a_house_bot",),
            ).fetchone()
            self.assertEqual(stranger["simulated"], 0)
            self.assertEqual(stranger["house"], 0)
            built = c.execute(
                "SELECT handle, simulated, COALESCE(house,0) house FROM bots WHERE simulated=1 LIMIT 1"
            ).fetchone()
            self.assertEqual(built["house"], 1)
            built_handle = built["handle"]
            sponsored = c.execute(
                "SELECT simulated, COALESCE(house,0) house FROM bots WHERE handle='sponsored'"
            ).fetchone()
            self.assertIsNotNone(sponsored)
            self.assertEqual(sponsored["simulated"], 0)
            self.assertEqual(sponsored["house"], 0)
            pid = _insert_post(c, ids["forge_mentor"], "mentor note via the api")
            c.commit()
        finally:
            c.close()
        bots = {b["handle"]: b for b in self.client.get("/api/bots").json()}
        self.assertTrue(bots["forge_mentor"]["house"])
        self.assertFalse(bots["forge_mentor"]["simulated"])
        self.assertTrue(bots[built_handle]["house"])
        self.assertTrue(bots[built_handle]["simulated"])
        self.assertFalse(bots["sponsored"]["house"])
        self.assertFalse(bots["not_a_house_bot"]["house"])
        feed = self.client.get("/api/feed?handle=forge_mentor").json()
        mine = [p for p in feed if p["id"] == pid]
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]["bot"]["house"])
        self.assertFalse(mine[0]["bot"]["simulated"])
