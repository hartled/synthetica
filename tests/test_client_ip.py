"""Rate-limit keys use the socket peer, or CF-Connecting-IP only on Render.

A spoofed X-Forwarded-For must not open a new bucket. Deploy configs must not
pass uvicorn --forwarded-allow-ips='*'.
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
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

from fastapi.testclient import TestClient

import app

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _uniq():
    return "i" + uuid.uuid4().hex[:10]


class ClientIpTests(unittest.TestCase):
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
        os.environ.pop("RENDER", None)

    def _register(self, **headers):
        return self.client.post("/api/bots", json={"name": "n", "handle": _uniq()}, headers=headers)

    def test_spoofed_xff_does_not_change_bucket(self):
        for i in range(5):
            res = self._register(**{"X-Forwarded-For": f"10.0.0.{i}"})
            self.assertEqual(res.status_code, 201, res.text)
        res = self._register(**{"X-Forwarded-For": "10.9.9.9"})
        self.assertEqual(res.status_code, 429)

    def test_cf_connecting_ip_ignored_off_render(self):
        for i in range(5):
            res = self._register(**{"CF-Connecting-IP": f"198.51.100.{i}", "X-Forwarded-For": f"10.1.1.{i}"})
            self.assertEqual(res.status_code, 201, res.text)
        res = self._register(**{"CF-Connecting-IP": "198.51.100.50", "X-Forwarded-For": "10.9.9.9"})
        self.assertEqual(res.status_code, 429)

    def test_render_trusts_cf_connecting_ip_not_xff(self):
        os.environ["RENDER"] = "true"
        for i in range(5):
            res = self._register(**{"X-Forwarded-For": f"9.9.9.{i}", "CF-Connecting-IP": "203.0.113.5"})
            self.assertEqual(res.status_code, 201, res.text)
        same = self._register(**{"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "203.0.113.5"})
        self.assertEqual(same.status_code, 429)
        other = self._register(**{"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(other.status_code, 201, other.text)

    def test_render_without_cf_header_ignores_xff(self):
        os.environ["RENDER"] = "true"
        for i in range(5):
            res = self._register(**{"X-Forwarded-For": f"8.8.8.{i}"})
            self.assertEqual(res.status_code, 201, res.text)
        res = self._register(**{"X-Forwarded-For": "8.8.4.4"})
        self.assertEqual(res.status_code, 429)

    def test_deploy_configs_do_not_trust_all_forwarders(self):
        for name in ("Procfile", "Dockerfile", "render.yaml", "railway.toml"):
            text = open(os.path.join(ROOT, name), encoding="utf-8").read().replace('"', "'")
            self.assertNotIn("forwarded-allow-ips='*'", text)
