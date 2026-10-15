"""Waitlist, Stripe-off, mocked Stripe-on, and the public sample routes.

These tests never call Stripe. Checkout and webhooks are mocked, or the
signature helper is exercised locally with a fake secret.
"""

import hashlib
import hmac
import json
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
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
for _key in (
    "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
    "WAITLIST_WEBHOOK_URL", "REPORT_PRICE_DISPLAY", "ADMIN_KEY",
):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient

import app
import reports

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}


def _email():
    return f"person-{uuid.uuid4().hex[:12]}@example.com"


class ReportHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(KEY_D, KEY_CEO)
        cls.client = TestClient(app.app)

    def setUp(self):
        app._hits.clear()
        for key in (
            "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET",
            "WAITLIST_WEBHOOK_URL", "REPORT_PRICE_DISPLAY", "REPORT_FROM_EMAIL",
            "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        ):
            os.environ.pop(key, None)

    def test_landing_sample_and_footer_when_payments_are_off(self):
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Coming soon. Join the waitlist", page.text)
        self.assertNotIn("TEST MODE", page.text)
        self.assertIn("Price", page.text)
        self.assertIn("$19 + applicable tax", page.text)
        self.assertIn('href="/refunds"', page.text)
        refunds = self.client.get("/refunds")
        self.assertEqual(refunds.status_code, 200, refunds.text)
        self.assertIn("Hartled1995@gmail.com", refunds.text)
        self.assertIn("within 7 days", refunds.text)
        self.assertIn('type="search"', refunds.text)
        self.assertIn("/reports/sample", page.text)
        self.assertIn('type="search"', page.text)
        self.assertIn('href="#main"', page.text)
        self.assertIn("max-width:768px", page.text)
        self.assertIn("no persistent disk", page.text)
        self.assertIn('aria-current="page"', page.text)
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/reports"', home.text)
        self.assertIn('href="/refunds"', home.text)
        sample = self.client.get("/reports/sample")
        self.assertEqual(sample.status_code, 200, sample.text)
        self.assertIn("Fictional persona", sample.text)
        self.assertIn("Alex Rivera", sample.text)
        self.assertIn("Speculative — not a job promise", sample.text)
        self.assertIn("Download sample PDF", sample.text)
        pdf = self.client.get("/reports/sample.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_price_display_is_configurable_and_escaped(self):
        os.environ["REPORT_PRICE_DISPLAY"] = 'USD 19 <script>'
        page = self.client.get("/reports")
        self.assertIn("USD 19 &lt;script&gt;", page.text)
        self.assertNotIn("USD 19 <script>", page.text)

    def test_one_stripe_variable_does_not_enable_checkout(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_only"
        self.assertFalse(reports.payments_enabled())
        page = self.client.get("/reports")
        self.assertIn("Coming soon. Join the waitlist", page.text)
        self.assertIn("TEST MODE", page.text)
        blocked = self.client.post("/api/reports/checkout", json=INTAKE)
        self.assertEqual(blocked.status_code, 403)

    def test_waitlist_validates_dedupes_rate_limits_and_forwards(self):
        forwarded = []

        def capture(url, payload, timeout=4):
            forwarded.append((url, payload))

        os.environ["WAITLIST_WEBHOOK_URL"] = "https://example.test/waitlist"
        email = _email()
        with mock.patch("reports.post_json", capture):
            first = self.client.post("/api/reports/waitlist", json={"email": email, "interests": "teaching"})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json(), {"ok": True})
            again = self.client.post("/api/reports/waitlist", json={"email": email.upper(), "interests": "teaching"})
            self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0][1]["email"], email)
        self.assertEqual(forwarded[0][1]["source"], "mindguild-report-waitlist")
        c = app.db()
        count = c.execute("SELECT COUNT(*) FROM report_waitlist WHERE email=?", (email,)).fetchone()[0]
        c.close()
        self.assertEqual(count, 1)
        bad = self.client.post("/api/reports/waitlist", json={"email": "not-an-email"})
        self.assertEqual(bad.status_code, 422)
        form = self.client.post("/api/reports/waitlist", data={"email": "bad", "current_job": "<script>"})
        self.assertEqual(form.status_code, 422)
        self.assertIn("&lt;script&gt;", form.text)
        self.assertNotIn('value="<script>"', form.text)
        os.environ.pop("WAITLIST_WEBHOOK_URL", None)
        app._hits.clear()
        for i in range(5):
            ok = self.client.post("/api/reports/waitlist", json={"email": _email()})
            self.assertEqual(ok.status_code, 200, ok.text)
        blocked = self.client.post("/api/reports/waitlist", json={"email": _email()})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertRaises(ValueError, reports.post_json, "file:///tmp/waitlist", {"email": "a@b.co"})

    def test_personal_generation_requires_admin_while_payments_are_off(self):
        missing = self.client.post("/api/reports/preview", json=INTAKE)
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.post("/api/reports/preview", json=INTAKE, headers={"Authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 401)
        preview = self.client.post(
            "/api/reports/preview",
            json=dict(INTAKE, current_job="<script>alert(1)</script>"),
            headers={"Authorization": "Bearer " + KEY_D},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertTrue(body["id"])
        self.assertNotIn("sample", body["id"])
        page = self.client.get(body["url"])
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Admin preview", page.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page.text)
        self.assertNotIn("<script>alert(1)</script>", page.text)
        pdf = self.client.get(body["url"] + "/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get("/reports/does-not-exist-token-value").status_code, 404)

    def test_mocked_checkout_unlocks_only_after_payment(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        self.assertTrue(reports.payments_enabled())
        landing = self.client.get("/reports")
        self.assertIn("Continue to checkout", landing.text)
        self.assertIn("TEST MODE", landing.text)
        self.assertNotIn("Coming soon. Join the waitlist", landing.text)
        closed = self.client.post("/api/reports/waitlist", json={"email": _email()})
        self.assertEqual(closed.status_code, 409)

        class Session:
            def __init__(self):
                self.id = "cs_test_paid123"
                self.url = "https://checkout.stripe.com/c/pay/cs_test_paid123"
                self.payment_status = "unpaid"
                self.metadata = {}
                self.client_reference_id = ""

        session = Session()

        def stripe_create(**kwargs):
            session.metadata = dict(kwargs.get("metadata") or {})
            session.client_reference_id = kwargs.get("client_reference_id")
            stripe_create.kwargs = kwargs
            return session

        def retrieve(sid):
            self.assertEqual(sid, session.id)
            return session

        with mock.patch("stripe.checkout.Session.create", stripe_create), mock.patch("stripe.checkout.Session.retrieve", retrieve):
            started = self.client.post("/api/reports/checkout", json=INTAKE)
            self.assertEqual(started.status_code, 200, started.text)
            payload = started.json()
            self.assertEqual(payload["checkout_url"], session.url)
            self.assertNotIn("id", payload)
            self.assertEqual(stripe_create.kwargs["line_items"], [{"price": "price_fake", "quantity": 1}])
            self.assertEqual(stripe_create.kwargs["automatic_tax"], {"enabled": True})
            self.assertEqual(stripe_create.kwargs["billing_address_collection"], "required")
            self.assertEqual(
                stripe_create.kwargs["idempotency_key"],
                "mindguild-report-" + session.client_reference_id,
            )
            submit = stripe_create.kwargs["custom_text"]["submit"]["message"]
            self.assertIn("7-day refund", submit)
            self.assertIn("http://testserver/refunds", submit)
            self.assertIn("/reports/success?session_id={CHECKOUT_SESSION_ID}", stripe_create.kwargs["success_url"])
            self.assertEqual(session.metadata.get("report_id"), session.client_reference_id)
            self.assertTrue(session.client_reference_id)
            unpaid = self.client.get("/reports/success?session_id=" + session.id)
            self.assertEqual(unpaid.status_code, 402)
            c = app.db()
            row = c.execute(
                "SELECT id, status, report_json FROM career_reports WHERE stripe_session_id=?",
                (session.id,),
            ).fetchone()
            c.close()
            self.assertEqual(row["status"], "pending")
            self.assertIsNone(row["report_json"])
            self.assertEqual(self.client.get("/reports/" + row["id"]).status_code, 404)
            forged = self.client.get(
                "/reports/success?session_id=" + session.id + "&report_id=" + row["id"],
            )
            self.assertEqual(forged.status_code, 402)
            session.payment_status = "paid"
            session.metadata = {"report_id": "not-the-stored-intake"}
            mismatched = self.client.get("/reports/success?session_id=" + session.id)
            self.assertEqual(mismatched.status_code, 404)
            c = app.db()
            still = c.execute("SELECT status, report_json FROM career_reports WHERE id=?", (row["id"],)).fetchone()
            c.close()
            self.assertEqual(still["status"], "pending")
            self.assertIsNone(still["report_json"])
            session.metadata = {"report_id": row["id"]}
            session.customer_details = {"email": "Buyer@Example.com"}
            unlocked = self.client.get("/reports/success?session_id=" + session.id, follow_redirects=False)
            self.assertEqual(unlocked.status_code, 200, unlocked.text)
            self.assertIn("Open your report", unlocked.text)
            self.assertIn("/reports/" + row["id"], unlocked.text)
            self.assertIn("/refunds", unlocked.text)
            page = self.client.get("/reports/" + row["id"])
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn("Junior data analyst", page.text)
            self.assertIn("Download PDF", page.text)
            c = app.db()
            saved = c.execute(
                "SELECT report_json, updated, buyer_email FROM career_reports WHERE id=?",
                (row["id"],),
            ).fetchone()
            c.close()
            self.assertEqual(saved["buyer_email"], "buyer@example.com")
            again = self.client.get("/reports/success?session_id=" + session.id, follow_redirects=False)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertIn("/reports/" + row["id"], again.text)
            c = app.db()
            replayed = c.execute(
                "SELECT status, report_json, updated FROM career_reports WHERE id=?",
                (row["id"],),
            ).fetchone()
            count = c.execute(
                "SELECT COUNT(*) FROM career_reports WHERE stripe_session_id=?",
                (session.id,),
            ).fetchone()[0]
            c.close()
            self.assertEqual(replayed["status"], "paid")
            self.assertEqual(replayed["report_json"], saved["report_json"])
            self.assertEqual(replayed["updated"], saved["updated"])
            self.assertEqual(count, 1)

            other = Session()
            other.id = "cs_test_hook123"
            other.url = "https://checkout.stripe.com/c/pay/cs_test_hook123"
            other.payment_status = "paid"

            def stripe_create_other(**kwargs):
                other.metadata = dict(kwargs.get("metadata") or {})
                other.client_reference_id = kwargs.get("client_reference_id")
                return other

            def retrieve_other(sid):
                self.assertEqual(sid, other.id)
                return other

        with mock.patch("stripe.checkout.Session.create", stripe_create_other), mock.patch("stripe.checkout.Session.retrieve", retrieve_other):
            second = self.client.post("/api/reports/checkout", json=INTAKE)
            self.assertEqual(second.status_code, 200, second.text)
            event = {
                "id": "evt_test_1",
                "type": "checkout.session.completed",
                "data": {"object": {"id": other.id, "object": "checkout.session"}},
            }
            raw = json.dumps(event).encode()
            bad = self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={"Stripe-Signature": "t=1,v1=deadbeef", "Content-Type": "application/json"},
            )
            self.assertEqual(bad.status_code, 400)
            stamp = str(int(time.time()))
            signed = stamp.encode() + b"." + raw
            mac = hmac.new(b"whsec_test", signed, hashlib.sha256).hexdigest()
            good = self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={"Stripe-Signature": f"t={stamp},v1={mac}", "Content-Type": "application/json"},
            )
            self.assertEqual(good.status_code, 200, good.text)
            self.assertTrue(good.json()["id"])
            saved = self.client.get("/reports/" + good.json()["id"])
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertNotIn("Admin preview", saved.text)


def _sign(secret, raw):
    stamp = str(int(time.time()))
    mac = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


class StripeWebhookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ReportHttpTests.client

    def setUp(self):
        ReportHttpTests.setUp(self)

    def _start(self, session_id):
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"

        class Session:
            def __init__(self):
                self.id = session_id
                self.url = "https://checkout.stripe.com/c/pay/" + session_id
                self.payment_status = "unpaid"
                self.metadata = {}
                self.client_reference_id = ""
                self.customer_details = {"email": "Buyer@Example.com"}

        session = Session()

        def stripe_create(**kwargs):
            session.metadata = dict(kwargs.get("metadata") or {})
            session.client_reference_id = kwargs.get("client_reference_id")
            stripe_create.kwargs = kwargs
            return session

        def retrieve(sid):
            self.assertEqual(sid, session.id)
            return session

        return session, stripe_create, retrieve

    def _event(self, session, event_id, event_type):
        return json.dumps({
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": session.id, "object": "checkout.session"}},
        }).encode()

    def _post(self, raw, signature):
        return self.client.post(
            "/api/stripe/webhook",
            content=raw,
            headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
        )

    def test_signature_replay_async_success_and_failure(self):
        session, stripe_create, retrieve = self._start("cs_test_wh1")
        sent = []

        def capture(req, timeout=8):
            sent.append(req.full_url)
            class Resp:
                def read(self):
                    return b"{}"
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return False
            return Resp()

        os.environ["RESEND_API_KEY"] = "re_test"
        os.environ["REPORT_FROM_EMAIL"] = "reports@example.com"
        with mock.patch("stripe.checkout.Session.create", stripe_create), mock.patch(
            "stripe.checkout.Session.retrieve", retrieve
        ), mock.patch("urllib.request.urlopen", capture):
            started = self.client.post("/api/reports/checkout", json=INTAKE)
            self.assertEqual(started.status_code, 200, started.text)
            self.assertEqual(stripe_create.kwargs["automatic_tax"], {"enabled": True})
            self.assertEqual(
                stripe_create.kwargs["idempotency_key"],
                "mindguild-report-" + session.client_reference_id,
            )
            raw = self._event(session, "evt_test_bad", "checkout.session.completed")
            bad = self._post(raw, "t=1,v1=deadbeef")
            self.assertEqual(bad.status_code, 400)

            unpaid = self._event(session, "evt_test_unpaid", "checkout.session.completed")
            ignored = self._post(unpaid, _sign("whsec_test", unpaid))
            self.assertEqual(ignored.status_code, 200, ignored.text)
            self.assertTrue(ignored.json().get("ignored"))
            c = app.db()
            row = c.execute(
                "SELECT id, status, report_json FROM career_reports WHERE stripe_session_id=?",
                (session.id,),
            ).fetchone()
            c.close()
            self.assertEqual(row["status"], "pending")
            self.assertIsNone(row["report_json"])

            session.payment_status = "paid"
            paid = self._event(session, "evt_test_paid", "checkout.session.completed")
            good = self._post(paid, _sign("whsec_test", paid))
            self.assertEqual(good.status_code, 200, good.text)
            self.assertEqual(good.json()["id"], row["id"])
            self.assertEqual(len(sent), 1)
            replay = self._post(paid, _sign("whsec_test", paid))
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json().get("duplicate"))
            self.assertEqual(len(sent), 1)
            c = app.db()
            saved = c.execute(
                "SELECT status, buyer_email, report_json FROM career_reports WHERE id=?",
                (row["id"],),
            ).fetchone()
            c.close()
            self.assertEqual(saved["status"], "paid")
            self.assertEqual(saved["buyer_email"], "buyer@example.com")
            self.assertTrue(saved["report_json"])

            failed_session, failed_create, failed_retrieve = self._start("cs_test_fail1")
            with mock.patch("stripe.checkout.Session.create", failed_create), mock.patch(
                "stripe.checkout.Session.retrieve", failed_retrieve
            ):
                self.assertEqual(self.client.post("/api/reports/checkout", json=INTAKE).status_code, 200)
                fail_raw = self._event(failed_session, "evt_test_fail", "checkout.session.async_payment_failed")
                failed = self._post(fail_raw, _sign("whsec_test", fail_raw))
                self.assertEqual(failed.status_code, 200, failed.text)
                self.assertEqual(failed.json()["status"], "failed")
                c = app.db()
                state = c.execute(
                    "SELECT id, status FROM career_reports WHERE stripe_session_id=?",
                    (failed_session.id,),
                ).fetchone()
                c.close()
                self.assertEqual(state["status"], "failed")
                self.assertEqual(self.client.get("/reports/" + state["id"]).status_code, 404)

            async_session, async_create, async_retrieve = self._start("cs_test_async1")
            async_session.payment_status = "paid"
            with mock.patch("stripe.checkout.Session.create", async_create), mock.patch(
                "stripe.checkout.Session.retrieve", async_retrieve
            ):
                self.assertEqual(self.client.post("/api/reports/checkout", json=INTAKE).status_code, 200)
                async_raw = self._event(
                    async_session, "evt_test_async", "checkout.session.async_payment_succeeded"
                )
                unlocked = self._post(async_raw, _sign("whsec_test", async_raw))
                self.assertEqual(unlocked.status_code, 200, unlocked.text)
                page = self.client.get("/reports/" + unlocked.json()["id"])
                self.assertEqual(page.status_code, 200, page.text)
                self.assertIn("Open your report", self.client.get(
                    "/reports/success?session_id=" + async_session.id
                ).text)

    def test_live_mode_without_webhook_secret_blocks_checkout(self):
        os.environ["STRIPE_SECRET_KEY"] = "sk_live_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_live_fake"
        self.assertFalse(reports.payments_enabled())
        page = self.client.get("/reports")
        self.assertIn("Coming soon. Join the waitlist", page.text)
        self.assertIn("webhook signing secret", page.text)
        self.assertNotIn("TEST MODE", page.text)
        blocked = self.client.post("/api/reports/checkout", json=INTAKE)
        self.assertEqual(blocked.status_code, 403)
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_live"
        self.assertTrue(reports.payments_enabled())
        self.assertIn("Continue to checkout", self.client.get("/reports").text)


if __name__ == "__main__":
    unittest.main()
