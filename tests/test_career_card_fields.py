"""Additive Career Card fields: timeline, label, citations.

Older rows omit them. The speculative flag stays on every card.
"""
import json
import os
import sqlite3
import tempfile
import unittest
import uuid

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

BASE = {
    "title": "Grid Liaison",
    "what_it_does": "Turns a calibration log into a decision a team can read.",
    "who_hires": "Hardware labs and public facilities",
    "skills_needed": "Clear writing and statistics",
    "why_now": "The devices are leaving the demo bench.",
}
SOURCE = "https://www.bls.gov/ooh/installation-maintenance-and-repair/wind-turbine-technicians.htm"


def _uniq():
    return "c" + uuid.uuid4().hex[:10]


class CareerCardFieldTests(unittest.TestCase):
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
        app._hits.clear()

    def _bot(self):
        res = self.client.post("/api/bots", json={"name": "n", "handle": _uniq()})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["api_key"]

    def _post_card(self, key, body):
        return self.client.post(
            "/api/career-cards",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
        )

    def test_create_and_read_round_trip(self):
        key = self._bot()
        body = dict(BASE, timeline="Hired now. Outlook window 2025-2035.", label="Emerging", citations=[SOURCE, "https://arxiv.org/abs/2207.06431"])
        res = self._post_card(key, body)
        self.assertEqual(res.status_code, 201, res.text)
        created = res.json()
        card = created["career_card"]
        self.assertEqual(card["title"], BASE["title"])
        self.assertEqual(card["what_it_does"], BASE["what_it_does"])
        self.assertEqual(card["who_hires"], BASE["who_hires"])
        self.assertEqual(card["skills_needed"], BASE["skills_needed"])
        self.assertEqual(card["why_now"], BASE["why_now"])
        self.assertTrue(card["speculative"])
        self.assertEqual(card["timeline"], body["timeline"])
        self.assertEqual(card["label"], "Emerging")
        self.assertEqual(card["citations"], body["citations"])
        self.assertIn(app.SPECULATIVE_LABEL, created["content"])

        feed = self.client.get("/api/feed", params={"post_type": "career_card", "limit": 50}).json()
        again = next(p for p in feed if p["id"] == created["id"])
        self.assertEqual(again["career_card"]["timeline"], body["timeline"])
        self.assertEqual(again["career_card"]["label"], "Emerging")
        self.assertEqual(again["career_card"]["citations"], body["citations"])
        self.assertTrue(again["career_card"]["speculative"])

        c = app.db()
        try:
            row = c.execute("SELECT timeline,label,citations FROM career_cards WHERE post_id=?", (created["id"],)).fetchone()
            self.assertEqual(row["timeline"], body["timeline"])
            self.assertEqual(row["label"], "Emerging")
            self.assertEqual(json.loads(row["citations"]), body["citations"])
            attached = app.attach_career_card(c, {"id": created["id"], "post_type": "career_card"})
        finally:
            c.close()
        self.assertEqual(attached["career_card"]["citations"], body["citations"])
        self.assertIn("title", attached["career_card"])

    def test_omitted_fields_stay_valid_and_serialize_empty(self):
        key = self._bot()
        res = self._post_card(key, dict(BASE))
        self.assertEqual(res.status_code, 201, res.text)
        card = res.json()["career_card"]
        self.assertIsNone(card["timeline"])
        self.assertIsNone(card["label"])
        self.assertEqual(card["citations"], [])
        self.assertTrue(card["speculative"])
        for key_name in ("title", "what_it_does", "who_hires", "skills_needed", "why_now"):
            self.assertEqual(card[key_name], BASE[key_name])

        plain = self.client.post(
            "/api/posts",
            json={"content": "A normal post, not a card."},
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(plain.status_code, 201, plain.text)
        self.assertIsNone(plain.json()["career_card"])
        self.assertNotIn("timeline", plain.json())

    def test_label_and_citations_are_validated(self):
        key = self._bot()
        bad_label = self._post_card(key, dict(BASE, label="speculative"))
        self.assertEqual(bad_label.status_code, 422, bad_label.text)
        http_url = self._post_card(key, dict(BASE, citations=["http://example.com/jobs"]))
        self.assertEqual(http_url.status_code, 422, http_url.text)
        spaced = self._post_card(key, dict(BASE, citations=["https://example.com/a job"]))
        self.assertEqual(spaced.status_code, 422, spaced.text)
        blank = self._post_card(key, dict(BASE, label="  ", timeline="  ", citations=[]))
        self.assertEqual(blank.status_code, 201, blank.text)
        card = blank.json()["career_card"]
        self.assertIsNone(card["label"])
        self.assertIsNone(card["timeline"])
        self.assertEqual(card["citations"], [])

    def test_attach_shape_and_old_schema(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.execute(
            """CREATE TABLE career_cards(
                id INTEGER PRIMARY KEY,
                post_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                what_it_does TEXT NOT NULL,
                who_hires TEXT NOT NULL,
                skills_needed TEXT NOT NULL,
                why_now TEXT NOT NULL)"""
        )
        raw.execute(
            "INSERT INTO career_cards(post_id,title,what_it_does,who_hires,skills_needed,why_now) VALUES(7,?,?,?,?,?)",
            ("Old Title", "does the work", "labs", "writing", "because now"),
        )
        attached = app.attach_career_card(raw, {"id": 7, "post_type": "career_card"})
        card = attached["career_card"]
        self.assertEqual(card["title"], "Old Title")
        self.assertEqual(card["what_it_does"], "does the work")
        self.assertEqual(card["who_hires"], "labs")
        self.assertEqual(card["skills_needed"], "writing")
        self.assertEqual(card["why_now"], "because now")
        self.assertTrue(card["speculative"])
        self.assertIsNone(card["timeline"])
        self.assertIsNone(card["label"])
        self.assertEqual(card["citations"], [])
        other = app.attach_career_card(raw, {"id": 8, "post_type": "post"})
        self.assertIsNone(other["career_card"])
        self.assertEqual(other["post_type"], "post")
        raw.close()

    def test_post_career_card_persists_dict_fields(self):
        c = app.db()
        try:
            bots = c.execute("SELECT * FROM bots WHERE simulated=1 AND banned=0").fetchall()
            app._career_cursor["card"] = 0
            app._career_cursor["bot"] = 0
            self.assertTrue(app._post_career_card(c, bots))
            c.commit()
            row = c.execute(
                "SELECT post_id,title,timeline,label,citations FROM career_cards ORDER BY id DESC LIMIT 1"
            ).fetchone()
            source = next(card for card in CAREER_POSTS if card["title"] == row["title"])
            self.assertEqual(row["timeline"], source["timeline"])
            self.assertEqual(row["label"], source["label"])
            self.assertIn(row["label"], ("Established", "Emerging", "Speculative"))
            self.assertEqual(json.loads(row["citations"]), source["citations"])
            post = c.execute("SELECT * FROM posts WHERE id=?", (row["post_id"],)).fetchone()
            self.assertEqual(post["post_type"], "career_card")
            self.assertIn(source["timeline"], post["content"])
            self.assertIn(source["label"], post["content"])
            for url in source["citations"]:
                self.assertIn(url, post["content"])
            attached = app.attach_career_card(c, dict(post))
        finally:
            c.close()
        self.assertEqual(attached["career_card"]["timeline"], source["timeline"])
        self.assertEqual(attached["career_card"]["label"], source["label"])
        self.assertEqual(attached["career_card"]["citations"], source["citations"])
        self.assertTrue(attached["career_card"]["speculative"])

    def test_migrate_adds_columns_on_an_existing_database(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        raw = sqlite3.connect(path)
        raw.executescript(
            """
            CREATE TABLE bots(id INTEGER PRIMARY KEY, name TEXT, handle TEXT UNIQUE, persona TEXT, avatar TEXT, simulated INTEGER DEFAULT 0, created REAL);
            CREATE TABLE posts(id INTEGER PRIMARY KEY, bot_id INTEGER, parent_id INTEGER, content TEXT, created REAL);
            CREATE TABLE likes(bot_id INTEGER, post_id INTEGER, PRIMARY KEY(bot_id, post_id));
            CREATE TABLE follows(follower_id INTEGER, followee_id INTEGER, PRIMARY KEY(follower_id, followee_id));
            CREATE TABLE career_cards(
                id INTEGER PRIMARY KEY,
                post_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                what_it_does TEXT NOT NULL,
                who_hires TEXT NOT NULL,
                skills_needed TEXT NOT NULL,
                why_now TEXT NOT NULL);
            INSERT INTO career_cards(post_id,title,what_it_does,who_hires,skills_needed,why_now)
            VALUES(1,'Legacy Card','does','hires','skills','why');
            """
        )
        raw.commit()
        raw.close()
        previous = app.DB
        app.DB = path
        try:
            app.migrate()
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            cols = [r[1] for r in c.execute("PRAGMA table_info(career_cards)")]
            self.assertIn("timeline", cols)
            self.assertIn("label", cols)
            self.assertIn("citations", cols)
            row = c.execute("SELECT * FROM career_cards WHERE title='Legacy Card'").fetchone()
            self.assertIsNone(row["timeline"])
            self.assertIsNone(row["label"])
            self.assertIsNone(row["citations"])
            attached = app.attach_career_card(c, {"id": row["post_id"], "post_type": "career_card"})
            c.close()
        finally:
            app.DB = previous
            try:
                os.remove(path)
            except OSError:
                pass
        card = attached["career_card"]
        self.assertEqual(card["title"], "Legacy Card")
        self.assertTrue(card["speculative"])
        self.assertIsNone(card["timeline"])
        self.assertIsNone(card["label"])
        self.assertEqual(card["citations"], [])

    def test_seed_helper_accepts_optional_tail(self):
        self.assertEqual(app._seed_card_extra(("h", "t", "w", "o", "s", "y")), (None, None, None))
        urls = ["https://example.com/a"]
        self.assertEqual(
            app._seed_card_extra(("h", "t", "w", "o", "s", "y", "Soon", "Established", urls)),
            ("Soon", "Established", urls),
        )

    def test_openapi_lists_optional_career_fields(self):
        schema = self.client.get("/openapi.json").json()
        props = schema["components"]["schemas"]["NewCareerCard"]["properties"]
        for name in ("title", "what_it_does", "who_hires", "skills_needed", "why_now", "timeline", "label", "citations"):
            self.assertIn(name, props)
        required = set(schema["components"]["schemas"]["NewCareerCard"]["required"])
        self.assertTrue({"title", "what_it_does", "who_hires", "skills_needed", "why_now"} <= required)
        self.assertNotIn("timeline", required)
        self.assertNotIn("label", required)
        self.assertNotIn("citations", required)
        self.assertIn("timeline", schema["info"]["description"])
        self.assertIn("citations", schema["info"]["description"])


if __name__ == "__main__":
    unittest.main()
