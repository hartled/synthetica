"""migrate_human_access must rebuild problem_replies when search.py's view and
triggers already reference it (production DBs from before PR #37)."""
import os
import sqlite3
import tempfile
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ.setdefault("DATABASE_PATH", _DB)
os.environ.setdefault("TICK_SECONDS", "100000")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

import app
import search


class MigrateProblemRepliesWithSearch(unittest.TestCase):
    def test_rebuild_with_existing_search_view(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE posts(id INTEGER PRIMARY KEY, human_id INTEGER)")
        c.execute("""CREATE TABLE problems(id INTEGER PRIMARY KEY, title TEXT, whats_wrong TEXT,
                     who_affected TEXT, best_answer TEXT, status TEXT, updated REAL)""")
        c.execute("CREATE TABLE problem_best_answer_history(id INTEGER PRIMARY KEY, problem_id INTEGER)")
        # Pre-#37 shape: bot_id NOT NULL, no human_id.
        c.execute("""CREATE TABLE problem_replies(id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL,
                     bot_id INTEGER NOT NULL, parent_id INTEGER, role TEXT NOT NULL,
                     content TEXT NOT NULL, created REAL)""")
        c.execute("INSERT INTO problems(id,title) VALUES(1,'p')")
        c.execute("INSERT INTO problem_replies VALUES(1,1,7,NULL,'skeptic','hello quantum',0)")
        if not search.fts5_available(c):
            self.skipTest("no fts5")
        search._build_problems(c)
        c.commit()

        app.migrate_human_access(c)
        c.commit()

        cols = {r[1]: r for r in c.execute("PRAGMA table_info(problem_replies)")}
        self.assertIn("human_id", cols)
        self.assertEqual(cols["bot_id"][3], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM problem_replies").fetchone()[0], 1)
        # Search rebuilds cleanly afterwards, as init() does on every start.
        search._build_problems(c)
        hits = c.execute("SELECT rowid FROM fts_problem_replies WHERE fts_problem_replies MATCH 'quantum'").fetchall()
        self.assertEqual([h[0] for h in hits], [1])
        # Idempotent.
        app.migrate_human_access(c)


if __name__ == "__main__":
    unittest.main()
