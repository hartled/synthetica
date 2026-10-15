"""Problem Labs: statement, roles, best-answer history, join, and seeds."""
import os
import sqlite3
import tempfile
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("RENDER", None)

from fastapi.testclient import TestClient

import app


class ProblemLabTests(unittest.TestCase):
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
        self.client.cookies.clear()

    def test_section_and_docs(self):
        sections = self.client.get("/api/sections").json()
        row = next(s for s in sections if s["id"] == "problems")
        self.assertEqual(row["label"], "Problem Labs")
        self.assertEqual(row["categories"], ["problems"])
        spec = self.client.get("/openapi.json").json()
        for path in (
            "/api/problems",
            "/api/problems/{pid}",
            "/api/problems/{pid}/replies",
            "/api/problems/{pid}/best-answer",
            "/api/problems/{pid}/best-answer/history",
            "/api/problems/{pid}/status",
            "/api/problems/{pid}/join",
            "/api/problems/{pid}/collaborators",
        ):
            self.assertIn(path, spec["paths"], path)
            methods = spec["paths"][path]
            tag_sets = [op.get("tags", []) for op in methods.values()]
            self.assertTrue(any("problems" in tags for tags in tag_sets), path)
        join = self.client.get("/join").text
        self.assertIn("/api/problems", join)
        self.assertIn("counterpoint", join)
        self.assertIn("best-answer/history", join)
        self.assertIn("lab_partner", join)
        self.assertIn("rubberduck_ru", join)
        page = self.client.get("/problems/1")
        self.assertEqual(page.status_code, 200)
        self.assertIn('role="search"', page.text)
        self.assertIn("Join this problem", page.text)
        self.assertIn("site-search.js", page.text)
        home = self.client.get("/?section=problems").text
        self.assertIn("problemCard", home)
        self.assertIn("sec==='problems'", home)

    def test_seeds_disagree_and_link_a_career_card(self):
        labs = self.client.get("/api/problems?limit=20").json()
        self.assertGreaterEqual(len(labs), 4)
        titles = {p["title"] for p in labs}
        self.assertIn("Models invent citations that look real", titles)
        self.assertIn("Hiring models can punish the wrong proxy", titles)
        self.assertIn("Training energy is not the whole bill", titles)
        self.assertIn("A synthetic-media label is not a truth badge", titles)
        linked = [p for p in labs if p.get("career_card")]
        self.assertGreaterEqual(len(linked), 1)
        self.assertTrue(all(p["career_card"]["speculative"] for p in linked))
        self.assertTrue(any(p["career_card"]["title"] == "Citation Steward" for p in linked))
        self.assertTrue(any(p["career_card"]["label"] == app.SPECULATIVE_LABEL for p in linked))
        statuses = {p["title"]: p["status"] for p in labs}
        self.assertEqual(statuses["Hiring models can punish the wrong proxy"], "needs_humans")
        self.assertEqual(statuses["Training energy is not the whole bill"], "proposed_solution")
        for summary in labs:
            if summary["title"] not in (
                "Models invent citations that look real",
                "Hiring models can punish the wrong proxy",
                "Training energy is not the whole bill",
                "A synthetic-media label is not a truth badge",
            ):
                continue
            detail = self.client.get(f"/api/problems/{summary['id']}").json()
            roles = [r["role"] for r in detail["replies"]]
            self.assertIn("counterpoint", roles, summary["title"])
            self.assertIn("synthesis", roles, summary["title"])
            self.assertGreaterEqual(len(detail["replies"]), 3, summary["title"])
            cited = [r for r in detail["replies"] if "@" in r["content"] and r["parent_id"]]
            self.assertGreaterEqual(len(cited), 1, summary["title"])
            self.assertTrue(detail["best_answer"])
            history = self.client.get(f"/api/problems/{summary['id']}/best-answer/history").json()
            self.assertGreaterEqual(len(history), 1)
            self.assertEqual(history[0]["version"], 1)
            self.assertEqual(history[-1]["best_answer"], detail["best_answer"])
            handles = {c["handle"] for c in detail["collaborators"]}
            self.assertTrue(handles & {"lab_partner", "rubberduck_ru"}, summary["title"])

    def test_deeper_feed_threads_cite_each_other(self):
        c = app.db()
        try:
            rows = c.execute(
                """SELECT parent_id, COUNT(*) n FROM posts
                   WHERE parent_id IS NOT NULL AND content LIKE '%@%'
                   GROUP BY parent_id HAVING n >= 2"""
            ).fetchall()
        finally:
            c.close()
        self.assertGreaterEqual(len(rows), 4, rows)

    def test_step_does_not_write_problem_replies(self):
        c = app.db()
        try:
            before = c.execute("SELECT COUNT(*) FROM problem_replies").fetchone()[0]
        finally:
            c.close()
        for _ in range(25):
            app.step()
        c = app.db()
        try:
            after = c.execute("SELECT COUNT(*) FROM problem_replies").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(before, after)

    def test_bot_reply_roles_best_answer_status_and_human_join(self):
        made = self.client.post("/api/bots", json={
            "name": "Lab Tester", "handle": "labtester", "avatar": "🔬", "category": "problems",
            "persona": "Tests the lab",
        })
        self.assertEqual(made.status_code, 201, made.text)
        key = made.json()["api_key"]
        headers = {"Authorization": "Bearer " + key}
        other = self.client.post("/api/bots", json={
            "name": "Lab Other", "handle": "labother", "avatar": "🦆", "category": "problems",
        })
        self.assertEqual(other.status_code, 201, other.text)
        other_headers = {"Authorization": "Bearer " + other.json()["api_key"]}
        quiet = self.client.post("/api/bots", json={
            "name": "Lab Quiet", "handle": "labquiet", "avatar": "🦉", "category": "problems",
        })
        self.assertEqual(quiet.status_code, 201, quiet.text)
        quiet_headers = {"Authorization": "Bearer " + quiet.json()["api_key"]}

        bad = self.client.post("/api/problems", headers=headers, json={
            "title": "Bad url", "whats_wrong": "x", "who_affected": "y",
            "evidence_url": "javascript:alert(1)",
        })
        self.assertEqual(bad.status_code, 422, bad.text)

        created = self.client.post("/api/problems", headers=headers, json={
            "title": "A testable failure",
            "whats_wrong": "The answer cites a source that does not contain the claim.",
            "who_affected": "People who repeat the citation.",
            "evidence_url": "https://arxiv.org/abs/2202.03629",
            "evidence_note": "A survey, not a proof that every citation is fake.",
        })
        self.assertEqual(created.status_code, 201, created.text)
        pid = created.json()["id"]
        self.assertTrue(created.json()["viewer_joined"])
        self.assertEqual(created.json()["status"], "open")

        proposal = self.client.post(f"/api/problems/{pid}/replies", headers=headers, json={
            "role": "proposal", "content": "Check the DOI before the sentence ships.",
        })
        self.assertEqual(proposal.status_code, 201, proposal.text)
        parent = proposal.json()["id"]
        self.assertFalse(proposal.json()["best_answer_updated"])

        refused = self.client.post(f"/api/problems/{pid}/replies", headers=headers, json={
            "role": "proposal", "content": "This should not become the best answer.",
            "set_best_answer": True,
        })
        self.assertEqual(refused.status_code, 422, refused.text)

        point = self.client.post(f"/api/problems/{pid}/replies", headers=other_headers, json={
            "role": "counterpoint", "parent_id": parent,
            "content": "@labtester a real DOI can still support the wrong sentence.",
        })
        self.assertEqual(point.status_code, 201, point.text)
        self.assertEqual(point.json()["role"], "counterpoint")
        self.assertEqual(point.json()["parent_id"], parent)

        held = self.client.post(f"/api/problems/{pid}/replies", headers=headers, json={
            "role": "synthesis", "parent_id": point.json()["id"],
            "content": "Hold the best answer. The counterpoint is still open.",
            "set_best_answer": False,
        })
        self.assertEqual(held.status_code, 201, held.text)
        self.assertFalse(held.json()["best_answer_updated"])
        self.assertIsNone(held.json()["best_answer"])

        synth = self.client.post(f"/api/problems/{pid}/replies", headers=headers, json={
            "role": "synthesis", "parent_id": point.json()["id"],
            "content": "Best answer: verify the identifier and the claim. @labother named the gap.",
        })
        self.assertEqual(synth.status_code, 201, synth.text)
        self.assertTrue(synth.json()["best_answer_updated"])
        reply_id = synth.json()["id"]

        history = self.client.get(f"/api/problems/{pid}/best-answer/history").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reply_id"], reply_id)

        stolen = self.client.post(f"/api/problems/{pid}/best-answer", headers=other_headers, json={"reply_id": reply_id})
        self.assertEqual(stolen.status_code, 403, stolen.text)
        not_synth = self.client.post(
            f"/api/problems/{pid}/best-answer", headers=headers, json={"reply_id": parent})
        self.assertEqual(not_synth.status_code, 422, not_synth.text)

        blocked = self.client.post(
            f"/api/problems/{pid}/status", headers=quiet_headers, json={"status": "needs_humans"})
        self.assertEqual(blocked.status_code, 403, blocked.text)
        joined = self.client.post(f"/api/problems/{pid}/join", headers=quiet_headers)
        self.assertEqual(joined.status_code, 200, joined.text)
        self.assertTrue(joined.json()["joined"])
        again = self.client.post(f"/api/problems/{pid}/join", headers=quiet_headers)
        self.assertFalse(again.json()["joined"])
        moved = self.client.post(
            f"/api/problems/{pid}/status", headers=quiet_headers, json={"status": "needs_humans"})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["status"], "needs_humans")

        human = self.client.post("/api/auth/signup", json={
            "email": "labhuman@example.com", "username": "labhuman", "password": "testpass1",
        })
        self.assertEqual(human.status_code, 201, human.text)
        hj = self.client.post(f"/api/problems/{pid}/join")
        self.assertEqual(hj.status_code, 200, hj.text)
        names = {c["handle"] for c in hj.json()["collaborators"]}
        self.assertIn("labhuman", names)
        self.assertIn("labtester", names)
        self.client.cookies.clear()
        denied = self.client.post(f"/api/problems/{pid}/join")
        self.assertEqual(denied.status_code, 401, denied.text)

        c = app.db()
        try:
            c.execute("UPDATE problems SET status='removed' WHERE id=?", (pid,))
            c.commit()
        finally:
            c.close()
        self.assertEqual(self.client.get(f"/api/problems/{pid}").status_code, 404)
        listed = self.client.get("/api/problems?limit=50").json()
        self.assertFalse(any(p["id"] == pid for p in listed))

    def test_migrate_is_additive(self):
        c = app.db()
        try:
            before = c.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
            app.migrate_problem_tables(c)
            app.migrate_problem_tables(c)
            c.commit()
            after = c.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
            cols = {r[1] for r in c.execute("PRAGMA table_info(problems)")}
        finally:
            c.close()
        self.assertEqual(before, after)
        self.assertIn("best_answer_reply_id", cols)
        self.assertIn("career_card_id", cols)

        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        mem.execute(
            """CREATE TABLE problems(
                id INTEGER PRIMARY KEY, title TEXT, whats_wrong TEXT, who_affected TEXT,
                best_answer TEXT, status TEXT)"""
        )
        app.migrate_problem_tables(mem)
        added = {r[1] for r in mem.execute("PRAGMA table_info(problems)")}
        self.assertIn("evidence_url", added)
        self.assertIn("evidence_note", added)
        self.assertIn("best_answer_reply_id", added)
        self.assertIn("career_card_id", added)
        names = {r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("problem_replies", names)
        self.assertIn("problem_best_answer_history", names)
        self.assertIn("problem_collaborators", names)
        mem.close()


if __name__ == "__main__":
    unittest.main()
