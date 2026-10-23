"""Career ticks are a fixed slice of step(), not simulated-to-simulated replies.

A seeded run of the public step() should land between 30% and 40% career
content: top-level Career Cards from CAREER_POSTS, posted by career-category
simulated bots. Likes and follows still happen on the other rolls.
"""
import os
import random
import tempfile
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_KEY"] = "test-admin"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

from fastapi.testclient import TestClient

import app
from personas_extra import CAREER_POSTS

TICKS = 2400
SEED = 20261015
REAL_API_HANDLES = ("forge_mentor", "blueprint_bea", "ledger_lin", "mentor_mae")


def _sim_on_sim(c, after_id):
    return c.execute(
        """SELECT r.id FROM posts r
           JOIN posts p ON p.id = r.parent_id
           JOIN bots rb ON rb.id = r.bot_id
           JOIN bots pb ON pb.id = p.bot_id
           WHERE r.id > ? AND rb.simulated=1 AND pb.simulated=1""",
        (after_id,),
    ).fetchall()


class CareerTickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The 30–40% career share is a property of the roll, measured across
        # thousands of immediate step() calls. The daily budget is a separate
        # gate and is covered in test_sim_budget.
        cls._prev_budget = os.environ.get("SIM_DAILY_POST_BUDGET")
        os.environ["SIM_DAILY_POST_BUDGET"] = "off"
        app.init()
        c = app.db()
        cls.seed_watermark = c.execute("SELECT COALESCE(MAX(id), 0) FROM posts").fetchone()[0]
        c.close()
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        c = app.db()
        try:
            # Lifespan steps and this test both write posts. Later tests share
            # this process, so put the database back to the seeded rows.
            c.execute("DELETE FROM career_cards WHERE post_id > ?", (cls.seed_watermark,))
            c.execute("DELETE FROM likes WHERE post_id > ?", (cls.seed_watermark,))
            c.execute("DELETE FROM posts WHERE id > ?", (cls.seed_watermark,))
            c.commit()
        finally:
            c.close()
        try:
            os.remove(_DB)
        except OSError:
            pass
        if cls._prev_budget is None:
            os.environ.pop("SIM_DAILY_POST_BUDGET", None)
        else:
            os.environ["SIM_DAILY_POST_BUDGET"] = cls._prev_budget

    def test_seeded_ticks_land_between_30_and_40_percent_career(self):
        c = app.db()
        try:
            watermark = c.execute("SELECT COALESCE(MAX(id), 0) FROM posts").fetchone()[0]
            for handle in REAL_API_HANDLES:
                row = c.execute("SELECT simulated FROM bots WHERE handle=?", (handle,)).fetchone()
                if row:
                    self.assertEqual(row["simulated"], 0, handle)
            random.seed(SEED)
            app._career_cursor["card"] = 0
            app._career_cursor["bot"] = 0
            for _ in range(TICKS):
                app.step()
            rows = c.execute(
                """SELECT p.id, p.parent_id, p.content, p.post_type, b.handle, b.category, b.simulated
                   FROM posts p JOIN bots b ON b.id = p.bot_id
                   WHERE p.id > ? ORDER BY p.id""",
                (watermark,),
            ).fetchall()
        finally:
            c.close()

        career = []
        for row in rows:
            is_card = (row["post_type"] == "career_card") or (row["content"] or "").startswith("Career Card —")
            if not is_card:
                continue
            self.assertIsNone(row["parent_id"])
            self.assertEqual(row["simulated"], 1)
            self.assertEqual(row["category"], "careers", row["handle"])
            career.append(row)

        share = len(career) / TICKS
        self.assertGreaterEqual(share, 0.30, f"career share {share:.4f} ({len(career)}/{TICKS})")
        self.assertLessEqual(share, 0.40, f"career share {share:.4f} ({len(career)}/{TICKS})")
        # Stash the measured share for the assertion message readers.
        self.measured_share = share

        titles = []
        for row in career:
            if row["post_type"] == "career_card":
                c = app.db()
                try:
                    title = c.execute(
                        "SELECT title FROM career_cards WHERE post_id=?", (row["id"],)
                    ).fetchone()["title"]
                finally:
                    c.close()
            else:
                title = row["content"].split("\n", 1)[0]
            titles.append(title)
        known = {card["title"] for card in CAREER_POSTS}
        self.assertTrue(titles)
        self.assertTrue(all(t in known or t.startswith("Career Card —") for t in titles), titles[:3])
        card_titles = [t for t in titles if t in known]
        self.assertGreaterEqual(len(set(card_titles)), 8, sorted(set(card_titles)))
        # Same card text is not posted twice inside the short global window.
        window = app._CAREER_REPEAT_WINDOW
        top_level = [row["content"] for row in rows if row["parent_id"] is None]
        for i in range(len(top_level)):
            if not (top_level[i] or "").startswith("Career Card —"):
                continue
            prior = top_level[max(0, i - window):i]
            self.assertNotIn(top_level[i], prior)
        authors = {row["handle"] for row in career if row["post_type"] == "career_card"}
        self.assertGreaterEqual(authors, {"novacareers", "jobforge"})

        c = app.db()
        try:
            self.assertEqual(_sim_on_sim(c, watermark), [])
            for handle in REAL_API_HANDLES:
                row = c.execute("SELECT simulated FROM bots WHERE handle=?", (handle,)).fetchone()
                if row:
                    self.assertEqual(row["simulated"], 0, handle)
        finally:
            c.close()

        feed = self.client.get("/api/feed", params={"limit": 30}).json()
        counts = {}
        for post in feed:
            if post.get("sponsored"):
                continue
            handle = post["bot"]["handle"]
            counts[handle] = counts.get(handle, 0) + 1
        self.assertTrue(counts)
        self.assertTrue(all(n <= 3 for n in counts.values()), counts)

        print(f"\nmeasured career share {share:.4f} ({len(career)}/{TICKS})")


if __name__ == "__main__":
    unittest.main()
