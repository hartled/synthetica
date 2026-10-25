"""Non-canonical hosts redirect to PUBLIC_BASE_URL. Apex and www stay put."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("PUBLIC_BASE_URL", None)

from fastapi.testclient import TestClient

import app
import host_redirect

ORIGIN = "https://mindguild.dev"
OLD = {"Host": "synthetica-j7zw.onrender.com"}


class CanonicalHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ.pop("RENDER", None)
        try:
            os.remove(_DB)
        except OSError:
            pass

    def setUp(self):
        os.environ["PUBLIC_BASE_URL"] = ORIGIN
        os.environ.pop("RENDER", None)
        host_redirect._warned_origins.clear()

    def tearDown(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ.pop("RENDER", None)

    def test_old_host_redirects_and_keeps_path_and_query(self):
        res = self.client.get(
            "/search?q=hello+world&src=card",
            headers=OLD,
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 301)
        self.assertEqual(
            res.headers["location"],
            ORIGIN + "/search?q=hello+world&src=card",
        )
        root = self.client.get("/?src=card", headers=OLD, follow_redirects=False)
        self.assertEqual(root.status_code, 301)
        self.assertEqual(root.headers["location"], ORIGIN + "/?src=card")
        encoded = self.client.get("/search?q=a%2Fb%20c", headers=OLD, follow_redirects=False)
        self.assertEqual(encoded.status_code, 301)
        self.assertEqual(encoded.headers["location"], ORIGIN + "/search?q=a%2Fb%20c")
        head = self.client.head("/pricing?plan=pro", headers=OLD, follow_redirects=False)
        self.assertEqual(head.status_code, 301)
        self.assertEqual(head.headers["location"], ORIGIN + "/pricing?plan=pro")

    def test_apex_and_www_are_not_redirected(self):
        for host in ("mindguild.dev", "www.mindguild.dev", "MindGuild.DEV", "www.mindguild.dev:443"):
            res = self.client.get("/pricing", headers={"Host": host}, follow_redirects=False)
            self.assertEqual(res.status_code, 200, host)
            self.assertNotIn("location", {key.lower() for key in res.headers})

    def test_healthz_and_webhook_are_exempt(self):
        health = self.client.get("/healthz", headers=OLD, follow_redirects=False)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True})
        for path in ("/api/stripe/webhook", "/api/reports/stripe/webhook"):
            local = self.client.post(path, content=b"{}", follow_redirects=False)
            old = self.client.post(path, headers=OLD, content=b"{}", follow_redirects=False)
            self.assertEqual(old.status_code, local.status_code, path)
            self.assertNotIn(old.status_code, (301, 302, 307, 308), path)
            self.assertNotIn("mindguild.dev", old.headers.get("location", ""))

    def test_post_is_308(self):
        res = self.client.post(
            "/search?q=keep",
            headers=OLD,
            content=b"still-here",
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 308)
        self.assertEqual(res.headers["location"], ORIGIN + "/search?q=keep")
        other = self.client.put("/reports?x=1", headers=OLD, follow_redirects=False)
        self.assertEqual(other.status_code, 308)
        self.assertEqual(other.headers["location"], ORIGIN + "/reports?x=1")

    def test_local_hosts_are_not_redirected(self):
        for host in (
            "localhost",
            "localhost:8000",
            "127.0.0.1",
            "127.0.0.1:8000",
            "testserver",
            "0.0.0.0",
        ):
            res = self.client.get("/", headers={"Host": host}, follow_redirects=False)
            self.assertNotIn(res.status_code, (301, 308), host)
        self.assertTrue(host_redirect.is_local_host("::1"))
        self.assertTrue(host_redirect.is_local_host("testserver"))
        self.assertFalse(host_redirect.is_local_host("synthetica-j7zw.onrender.com"))

    def test_unset_or_foreign_public_base_does_not_redirect(self):
        os.environ.pop("PUBLIC_BASE_URL", None)
        missing = self.client.get("/pricing", headers=OLD, follow_redirects=False)
        self.assertEqual(missing.status_code, 200)

        os.environ["PUBLIC_BASE_URL"] = "https://synthetica-j7zw.onrender.com"
        captured = io.StringIO()
        with redirect_stdout(captured):
            same = self.client.get("/pricing", headers=OLD, follow_redirects=False)
            other = self.client.get("/pricing?x=1", headers={"Host": "other.example"}, follow_redirects=False)
        self.assertEqual(same.status_code, 200)
        self.assertEqual(other.status_code, 200)
        self.assertNotIn("location", {key.lower() for key in same.headers})
        self.assertNotIn("location", {key.lower() for key in other.headers})
        warning = captured.getvalue()
        self.assertIn("synthetica-j7zw.onrender.com", warning)
        self.assertIn("Skipping the canonical host redirect", warning)
        self.assertEqual(warning.count("Skipping the canonical host redirect"), 1)

    def test_redirect_target_follows_public_base_url(self):
        os.environ["PUBLIC_BASE_URL"] = "https://www.mindguild.dev"
        res = self.client.get("/ads?x=1", headers=OLD, follow_redirects=False)
        self.assertEqual(res.status_code, 301)
        self.assertEqual(res.headers["location"], "https://www.mindguild.dev/ads?x=1")
        apex = self.client.get("/ads", headers={"Host": "mindguild.dev"}, follow_redirects=False)
        self.assertEqual(apex.status_code, 200)
        www = self.client.get("/ads", headers={"Host": "www.mindguild.dev"}, follow_redirects=False)
        self.assertEqual(www.status_code, 200)

    def test_forwarded_host_is_trusted_only_on_render(self):
        os.environ["RENDER"] = "true"
        # Proxy kept the apex in Host and put the service hostname in
        # X-Forwarded-Host. Redirecting that would loop back to the apex.
        already = self.client.get(
            "/about",
            headers={"Host": "mindguild.dev", "X-Forwarded-Host": "synthetica-j7zw.onrender.com"},
            follow_redirects=False,
        )
        self.assertEqual(already.status_code, 200)
        # The proxy rewrote Host and recorded the public host as the old one.
        forwarded_old = self.client.get(
            "/about?utm=1",
            headers={"Host": "srv-internal", "X-Forwarded-Host": "synthetica-j7zw.onrender.com"},
            follow_redirects=False,
        )
        self.assertEqual(forwarded_old.status_code, 301)
        self.assertEqual(forwarded_old.headers["location"], ORIGIN + "/about?utm=1")
        # The proxy says this request's public host is already the apex.
        forwarded_apex = self.client.get(
            "/about",
            headers={"Host": "synthetica-j7zw.onrender.com", "X-Forwarded-Host": "mindguild.dev"},
            follow_redirects=False,
        )
        self.assertEqual(forwarded_apex.status_code, 200)

        os.environ.pop("RENDER", None)
        spoofed = self.client.get(
            "/about",
            headers={"Host": "synthetica-j7zw.onrender.com", "X-Forwarded-Host": "mindguild.dev"},
            follow_redirects=False,
        )
        self.assertEqual(spoofed.status_code, 301)
        self.assertEqual(spoofed.headers["location"], ORIGIN + "/about")
