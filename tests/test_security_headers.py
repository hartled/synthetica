"""Security headers, with a CSP that still allows the site's inline scripts."""
import os
import tempfile
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_KEY"] = "test-admin"
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("RENDER", None)
os.environ["COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient

import app


class SecurityHeaderTests(unittest.TestCase):
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

    def test_homepage_headers_allow_inline(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertEqual(res.headers["x-frame-options"], "DENY")
        self.assertEqual(res.headers["referrer-policy"], "strict-origin-when-cross-origin")
        csp = res.headers["content-security-policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("img-src 'self' data:", csp)
        self.assertIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("strict-transport-security", {k.lower() for k in res.headers})

    def test_hsts_only_when_https(self):
        res = self.client.get("/", headers={"x-forwarded-proto": "https"})
        self.assertEqual(res.headers["strict-transport-security"], "max-age=31536000")

    def test_docs_csp_keeps_swagger(self):
        res = self.client.get("/docs")
        self.assertEqual(res.status_code, 200)
        csp = res.headers["content-security-policy"]
        self.assertIn("https://cdn.jsdelivr.net", csp)
        self.assertIn("'unsafe-inline'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
