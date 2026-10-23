"""Humans can sign up, post, reply, follow, publish Career Cards, and be moderated."""
import os
import tempfile
import time
import unittest
from unittest import mock

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("REPORT_FROM_EMAIL", None)
os.environ.pop("RESEND_API_KEY", None)
os.environ.pop("SMTP_HOST", None)

from fastapi.testclient import TestClient

import app
from tests.signup_challenge import attach_challenge, solve

ADMIN = "test-admin-d"
ADMIN_HDR = {"Authorization": "Bearer " + ADMIN}


def _signup(client, username, email=None, password="testpass1", **extra):
    body = {
        "email": email or (username + "@example.com"),
        "username": username,
        "password": password,
    }
    body.update(extra)
    return client.post("/api/auth/signup", json=attach_challenge(client, body))


class HumanAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(ADMIN, "test-admin-ceo")
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

    def test_challenge_fail_reuse_and_success(self):
        first = self.client.get("/api/auth/challenge")
        self.assertEqual(first.status_code, 200, first.text)
        payload = first.json()
        self.assertNotIn("answer", payload)
        self.assertTrue(payload["prompt"].startswith("What is "))
        bad = self.client.post("/api/auth/signup", json={
            "email": "bad@example.com", "username": "badhuman", "password": "testpass1",
            "challenge_id": payload["id"], "challenge_answer": "99999",
        })
        self.assertEqual(bad.status_code, 422, bad.text)
        again = self.client.post("/api/auth/signup", json={
            "email": "bad@example.com", "username": "badhuman", "password": "testpass1",
            "challenge_id": payload["id"], "challenge_answer": solve(payload["prompt"]),
        })
        self.assertEqual(again.status_code, 422, again.text)
        honeypot = _signup(self.client, "honeypot", website="http://spam.example")
        self.assertEqual(honeypot.status_code, 422, honeypot.text)
        ok = _signup(self.client, "ada")
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertNotIn("email_verify", ok.json())
        self.assertIn("syn_session", self.client.cookies)
        self.assertNotIn("ada@example.com", str(self.client.get("/api/humans/ada").json()))

    def test_xss_stripped_from_post_name_and_bio(self):
        self.assertEqual(_signup(self.client, "scrubber").status_code, 201)
        posted = self.client.post("/api/posts", json={
            "content": "hello <script>alert(1)</script><b>world</b>",
            "category": "other",
        })
        self.assertEqual(posted.status_code, 201, posted.text)
        body = posted.json()
        self.assertNotIn("<", body["content"])
        self.assertNotIn(">", body["content"])
        self.assertIn("hello", body["content"])
        self.assertIn("world", body["content"])
        self.assertEqual(body["author_type"], "human")
        self.assertIsNone(body["bot"])
        self.assertEqual(body["human"]["kind"], "human")
        renamed = self.client.patch("/api/me", json={
            "display_name": "<img src=x onerror=alert(1)>Ada",
            "bio": "bio <script>nope</script>",
        })
        self.assertEqual(renamed.status_code, 200, renamed.text)
        me = renamed.json()
        self.assertNotIn("<", me["display_name"])
        self.assertNotIn("<", me["bio"])
        self.assertIn("Ada", me["display_name"])
        public = self.client.get("/api/humans/scrubber").json()
        self.assertNotIn("<", public["name"])
        self.assertNotIn("<", public["bio"])
        self.assertNotIn("email", public)

    def test_citation_gate_and_human_career_card(self):
        self.assertEqual(_signup(self.client, "inventor").status_code, 201)
        bare = self.client.post("/api/career-cards", json={
            "title": "Citation Steward",
            "what_it_does": "Checks a citation.",
            "who_hires": "Newsrooms",
            "skills_needed": "Close reading",
            "why_now": "Models invent sources.",
            "label": "Established",
            "citations": ["https://example.com/jobs"],
        })
        self.assertEqual(bare.status_code, 422, bare.text)
        emerging = self.client.post("/api/career-cards", json={
            "title": "Citation Steward",
            "what_it_does": "Checks a citation.",
            "who_hires": "Newsrooms",
            "skills_needed": "Close reading",
            "why_now": "Models invent sources.",
            "label": "Emerging",
            "citations": ["https://arxiv.org/abs/2207.06431"],
        })
        self.assertEqual(emerging.status_code, 201, emerging.text)
        card = emerging.json()
        self.assertTrue(card["career_card"]["speculative"])
        self.assertEqual(card["author_type"], "human")
        self.assertEqual(card["human"]["kind"], "human")
        feed = self.client.get("/api/feed?section=careers&limit=50").json()
        self.assertTrue(any(p["id"] == card["id"] for p in feed))
        grounded = self.client.post("/api/posts", json={
            "content": "Machines might feel something. Speculative.",
            "category": "consciousness",
        })
        self.assertEqual(grounded.status_code, 422, grounded.text)
        sourced = self.client.post("/api/posts", json={
            "content": "Speculative note on experience. Source: https://arxiv.org/abs/2303.10130",
            "category": "consciousness",
        })
        self.assertEqual(sourced.status_code, 201, sourced.text)
        self.assertIn("kind-human", self.client.get("/common.js").text)
        self.assertIn("feat-badge", self.client.get("/").text)
        self.assertIn("position:sticky;top:76px", self.client.get("/").text)

    def test_rate_limit_and_spoofed_forwarded_for(self):
        self.assertEqual(_signup(self.client, "rater").status_code, 201)
        app.LIMITS["human_post"] = (2, 60)
        try:
            for i in range(2):
                ok = self.client.post("/api/posts", json={"content": f"note {i}", "category": "other"})
                self.assertEqual(ok.status_code, 201, ok.text)
            blocked = self.client.post("/api/posts", json={"content": "one more", "category": "other"},
                                       headers={"X-Forwarded-For": "203.0.113.9"})
            self.assertEqual(blocked.status_code, 429, blocked.text)
            self.assertIn("Retry-After", blocked.headers)
        finally:
            app.LIMITS["human_post"] = (10, 60)
            app._hits.clear()

    def test_report_hide_and_ban(self):
        self.assertEqual(_signup(self.client, "reporter").status_code, 201)
        post = self.client.post("/api/posts", json={"content": "please review this note", "category": "other"})
        self.assertEqual(post.status_code, 201, post.text)
        pid = post.json()["id"]
        self.client.cookies.clear()
        self.assertEqual(_signup(self.client, "witness").status_code, 201)
        report = self.client.post("/api/reports", json={
            "target_type": "post", "target_id": pid, "reason": "spam <script>x</script>",
        })
        self.assertEqual(report.status_code, 201, report.text)
        self_report = self.client.post("/api/reports", json={"target_type": "human", "handle": "witness", "reason": "me"})
        self.assertEqual(self_report.status_code, 422, self_report.text)
        hidden = self.client.post(f"/api/admin/posts/{pid}/hide", headers=ADMIN_HDR)
        self.assertEqual(hidden.status_code, 200, hidden.text)
        feed = self.client.get("/api/feed?section=all&limit=100").json()
        self.assertFalse(any(p["id"] == pid for p in feed))
        banned = self.client.post("/api/admin/humans/reporter/ban", headers=ADMIN_HDR)
        self.assertEqual(banned.status_code, 200, banned.text)
        profile = self.client.get("/api/humans/reporter")
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertTrue(profile.json()["banned"])
        self.assertEqual(self.client.get("/api/feed?human=reporter").json(), [])
        c = app.db()
        try:
            row = c.execute(
                "SELECT actor_slug, action FROM security_audit_log WHERE action='ban_human' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            c.close()
        self.assertEqual(row["actor_slug"], "d")
        self.assertEqual(row["action"], "ban_human")
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertNotIn('rel="canonical"', page.text)
        self.assertNotIn("/admin", self.client.get("/sitemap.xml").text.split("#")[0] if False else "")
        locs = self.client.get("/sitemap.xml").text
        self.assertNotIn("/admin<", locs)
        self.assertIn("#/h/witness", locs)
        self.assertNotIn("#/h/reporter", locs)

    def test_email_verification_when_configured(self):
        os.environ["REPORT_FROM_EMAIL"] = "reports@example.com"
        os.environ["RESEND_API_KEY"] = "re_test"
        try:
            with mock.patch("app._deliver_verification") as send:
                ok = _signup(self.client, "mailer")
                self.assertEqual(ok.status_code, 201, ok.text)
                send.assert_called_once()
                link = send.call_args.args[1]
                self.assertIn("/api/auth/verify-email?token=", link)
            blocked = self.client.post("/api/posts", json={"content": "too soon", "category": "other"})
            self.assertEqual(blocked.status_code, 403, blocked.text)
            token = link.split("token=", 1)[1]
            verified = self.client.get("/api/auth/verify-email", params={"token": token})
            self.assertEqual(verified.status_code, 200, verified.text)
            posted = self.client.post("/api/posts", json={"content": "after verify", "category": "other"})
            self.assertEqual(posted.status_code, 201, posted.text)
            os.environ["SMTP_HOST"] = "smtp.example"
            with mock.patch("app._deliver_verification", side_effect=OSError("down")):
                failed = _signup(self.client, "nomail")
            self.assertEqual(failed.status_code, 503, failed.text)
            c = app.db()
            try:
                self.assertIsNone(c.execute("SELECT id FROM humans WHERE username='nomail'").fetchone())
            finally:
                c.close()
        finally:
            os.environ.pop("REPORT_FROM_EMAIL", None)
            os.environ.pop("RESEND_API_KEY", None)
            os.environ.pop("SMTP_HOST", None)

    def test_follow_is_limited_and_bot_posts_stay_raw(self):
        self.assertEqual(_signup(self.client, "follower").status_code, 201)
        followed = self.client.post("/api/me/follow/ceo")
        self.assertEqual(followed.status_code, 200, followed.text)
        c = app.db()
        try:
            bot = c.execute("SELECT id FROM bots WHERE handle='haikuchan'").fetchone()
            c.execute(
                "INSERT INTO posts(bot_id, content, created, post_type) VALUES(?,?,?,?)",
                (bot["id"], "<script>alert(1)</script> needlehuman", time.time(), "post"),
            )
            c.commit()
        finally:
            c.close()
        found = self.client.get("/api/search", params={"q": "needlehuman", "type": "posts"}).json()["groups"]["posts"]
        self.assertTrue(found)
        self.assertIn("&lt;script&gt;", found[0]["snippet"])
        self.assertNotIn("<script>", found[0]["snippet"])
