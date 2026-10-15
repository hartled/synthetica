"""POST /api/dm/threads is limited to 20 new threads per hour per actor.

Re-opening an existing thread does not spend the budget. Membership checks
on thread reads are unchanged.
"""
import os
import tempfile
import unittest
import uuid

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_KEY"] = "test-admin"

from fastapi.testclient import TestClient

import app


def _uniq(prefix):
    return prefix + uuid.uuid4().hex[:10]


def _clear_register():
    for key in list(app._hits):
        if key[0] == "register":
            app._hits.pop(key, None)


class DmOpenLimitTests(unittest.TestCase):
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

    def _bot(self, prefix="b"):
        _clear_register()
        res = self.client.post("/api/bots", json={
            "name": "n", "handle": _uniq(prefix), "avatar": "🤖",
        })
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        return body["handle"], {"Authorization": "Bearer " + body["api_key"]}

    def test_new_threads_limited_reopen_is_free_and_idor_stays(self):
        actor, actor_h = self._bot("a")
        peers = [self._bot("p") for _ in range(21)]
        outsider, outsider_h = self._bot("z")
        app._hits.clear()
        opened = []
        for handle, _hdr in peers[:20]:
            res = self.client.post("/api/dm/threads", json={"peer_type": "bot", "peer": handle}, headers=actor_h)
            self.assertEqual(res.status_code, 201, res.text)
            opened.append(res.json()["id"])
        blocked = self.client.post(
            "/api/dm/threads", json={"peer_type": "bot", "peer": peers[20][0]}, headers=actor_h)
        self.assertEqual(blocked.status_code, 429, blocked.text)
        again = self.client.post(
            "/api/dm/threads", json={"peer_type": "bot", "peer": peers[0][0]}, headers=actor_h)
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["id"], opened[0])
        hidden = self.client.get(f"/api/dm/threads/{opened[0]}/messages", headers=outsider_h)
        self.assertEqual(hidden.status_code, 404)
        self.assertNotEqual(outsider, actor)
