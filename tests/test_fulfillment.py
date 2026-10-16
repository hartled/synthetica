"""Placement fulfillment: Featured listings, ad sponsorships, renewals, replays."""

import hashlib
import hmac
import json
import os
import re
import time
import unittest
import uuid
from unittest import mock

_fd, _DB = __import__("tempfile").mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

from fastapi.testclient import TestClient

import app
import funnel
import fulfillment

KEY = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR = {"Authorization": "Bearer " + KEY}
DAY = 86400
INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}


def _sign(secret, raw):
    stamp = str(int(time.time()))
    mac = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


class FulfillmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.bootstrap_named_admin_keys(KEY, KEY_CEO)
        cls.client = TestClient(app.app)
        cls.client.__enter__()
        created = cls.client.post("/api/bots", json={
            "name": "Pay Bot",
            "handle": "ChefByte",
            "persona": "pays for a listing",
            "avatar": "🤖",
            "category": "other",
        })
        if created.status_code == 409:
            cls.handle = "ChefByte"
        else:
            assert created.status_code == 201, created.text
            cls.handle = created.json()["handle"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        app._hits.clear()
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
        os.environ["STRIPE_PRICE_ID"] = "price_fake"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        c = app.db()
        try:
            fulfillment.ensure_tables(c)
            funnel.ensure_table(c)
            c.execute("UPDATE bots SET featured_until=NULL, banned=0 WHERE handle=?", (self.handle,))
            c.execute("DELETE FROM sponsorships")
            c.execute("DELETE FROM fulfillment_audit")
            c.execute("DELETE FROM fulfillment_applied")
            c.execute("DELETE FROM stripe_subscriptions")
            c.execute("DELETE FROM funnel_events")
            c.commit()
        finally:
            c.close()
        funnel.reset_user_agent(funnel.note_user_agent(""))

    def tearDown(self):
        for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(key, None)

    def _id(self, prefix):
        return prefix + "_" + uuid.uuid4().hex[:12]

    def _featured(self):
        c = app.db()
        try:
            row = c.execute("SELECT featured_until FROM bots WHERE handle=?", (self.handle,)).fetchone()
        finally:
            c.close()
        return row["featured_until"]

    def _sponsor(self, tier):
        c = app.db()
        try:
            row = c.execute(
                "SELECT active_until FROM sponsorships WHERE handle=? AND tier=?",
                (self.handle, tier),
            ).fetchone()
        finally:
            c.close()
        return None if row is None else row["active_until"]

    def _audits(self):
        c = app.db()
        try:
            rows = c.execute(
                "SELECT event_id, object_id, tier, handle, action, result, amount FROM fulfillment_audit ORDER BY id"
            ).fetchall()
        finally:
            c.close()
        return [dict(row) for row in rows]

    def _purchases(self):
        c = app.db()
        try:
            rows = c.execute(
                "SELECT src, report_id FROM funnel_events WHERE event='purchase_complete' ORDER BY id"
            ).fetchall()
        finally:
            c.close()
        return [dict(row) for row in rows]

    def _mapping(self, sub_id):
        c = app.db()
        try:
            row = c.execute(
                "SELECT handle, tier FROM stripe_subscriptions WHERE subscription_id=?",
                (sub_id,),
            ).fetchone()
        finally:
            c.close()
        return None if row is None else dict(row)

    def _session(self, sid, tier, handle="@ChefByte", paid=True, quantity=None, subscription=None, amount=900):
        session = {
            "id": sid,
            "object": "checkout.session",
            "payment_status": "paid" if paid else "unpaid",
            "metadata": {"tier": tier},
            "custom_fields": [{"key": "bothandle", "type": "text", "text": {"value": handle}}],
            "amount_total": amount,
            "subscription": subscription,
        }
        expanded = dict(session)
        if quantity is not None:
            expanded["line_items"] = {"object": "list", "data": [{"quantity": quantity}]}
        return session, expanded

    def _post_checkout(self, session, expanded, event_id, event_type="checkout.session.completed"):
        def retrieve(sid, expand=None, **kwargs):
            self.assertEqual(sid, session["id"])
            retrieve.expanded = expand
            if expand:
                return expanded
            return session

        retrieve.expanded = None
        raw = json.dumps({
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": session["id"], "object": "checkout.session"}},
        }).encode()
        with mock.patch("stripe.checkout.Session.retrieve", retrieve):
            response = self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={"Stripe-Signature": _sign("whsec_test", raw), "Content-Type": "application/json"},
            )
        response.expanded = retrieve.expanded
        return response

    def _post_invoice(self, invoice, event_id, subscription=None):
        raw = json.dumps({
            "id": event_id,
            "type": "invoice.paid",
            "data": {"object": invoice},
        }).encode()

        def explode(*args, **kwargs):
            raise AssertionError("Checkout Session retrieve is not used for invoices")

        with mock.patch("stripe.checkout.Session.retrieve", explode), mock.patch(
            "stripe.Subscription.retrieve", subscription or explode
        ):
            return self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={"Stripe-Signature": _sign("whsec_test", raw), "Content-Type": "application/json"},
            )

    def test_featured_week_normalizes_handle_and_extends_seven_days(self):
        sid = self._id("cs_week")
        session, expanded = self._session(sid, "FEATURED_LINK_WEEK", handle=" @CHEFBYTE ", amount=900)
        event_id = self._id("evt_week")
        response = self._post_checkout(session, expanded, event_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["fulfilled"], True)
        self.assertEqual(body["tier"], "FEATURED_LINK_WEEK")
        self.assertEqual(body["handle"], self.handle)
        self.assertIsNone(response.expanded)
        self.assertAlmostEqual(self._featured(), time.time() + 7 * DAY, delta=5)
        self.assertEqual(self._sponsor("AD_LINK_WEEK"), None)
        self.assertEqual(self._purchases(), [{"src": "FEATURED_LINK_WEEK", "report_id": None}])
        audit = self._audits()
        self.assertEqual(audit[0]["result"], "ok")
        self.assertEqual(audit[0]["action"], "extend")
        self.assertEqual(audit[0]["amount"], 900)
        self.assertEqual(audit[0]["handle"], self.handle)
        self.assertNotIn("email", json.dumps(audit).lower())

    def test_featured_month_stores_subscription_mapping(self):
        sid = self._id("cs_month")
        sub = self._id("sub_month")
        session, expanded = self._session(
            sid, "FEATURED_LINK_MONTH", subscription=sub, amount=2900
        )
        response = self._post_checkout(session, expanded, self._id("evt_month"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tier"], "FEATURED_LINK_MONTH")
        self.assertAlmostEqual(self._featured(), time.time() + 31 * DAY, delta=5)
        self.assertEqual(self._mapping(sub), {"handle": self.handle, "tier": "FEATURED_LINK_MONTH"})

    def test_ad_day_uses_line_item_quantity(self):
        sid = self._id("cs_day")
        session, expanded = self._session(sid, "AD_LINK_DAY", quantity=3, amount=1500)
        response = self._post_checkout(session, expanded, self._id("evt_day"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tier"], "AD_LINK_DAY")
        self.assertEqual(response.expanded, ["line_items"])
        self.assertAlmostEqual(self._sponsor("AD_LINK_DAY"), time.time() + 3 * DAY, delta=5)
        self.assertIsNone(self._featured())
        listed = self.client.get("/api/sponsors").json()["sponsors"]
        self.assertEqual(listed[0]["handle"], self.handle)
        self.assertEqual(listed[0]["tier"], "AD_LINK_DAY")
        self.assertEqual(listed[0]["label"], "Sponsored post")

    def test_ad_week_activates_seven_days(self):
        sid = self._id("cs_adweek")
        session, expanded = self._session(sid, "AD_LINK_WEEK", amount=2900)
        response = self._post_checkout(session, expanded, self._id("evt_adweek"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.expanded)
        self.assertAlmostEqual(self._sponsor("AD_LINK_WEEK"), time.time() + 7 * DAY, delta=5)
        listed = self.client.get("/api/sponsors").json()["sponsors"]
        self.assertEqual(listed[0]["label"], "7-day sponsored pack")

    def test_ad_section_activates_thirty_one_days(self):
        sid = self._id("cs_section")
        sub = self._id("sub_section")
        session, expanded = self._session(sid, "AD_LINK_SECTION", subscription=sub, amount=9900)
        response = self._post_checkout(session, expanded, self._id("evt_section"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertAlmostEqual(self._sponsor("AD_LINK_SECTION"), time.time() + 31 * DAY, delta=5)
        self.assertEqual(self._mapping(sub), {"handle": self.handle, "tier": "AD_LINK_SECTION"})
        self.assertEqual(
            self.client.get("/api/sponsors").json()["sponsors"][0]["label"],
            "Section sponsorship",
        )

    def test_renewal_extends_from_the_current_end(self):
        sid = self._id("cs_renew")
        sub = self._id("sub_renew")
        session, expanded = self._session(sid, "FEATURED_LINK_MONTH", subscription=sub, amount=2900)
        first = self._post_checkout(session, expanded, self._id("evt_renew_checkout"))
        self.assertEqual(first.status_code, 200, first.text)
        start = self._featured()
        invoice = {
            "id": self._id("in_cycle"),
            "object": "invoice",
            "billing_reason": "subscription_cycle",
            "amount_paid": 2900,
            "subscription": sub,
        }
        renewed = self._post_invoice(invoice, self._id("evt_cycle"))
        self.assertEqual(renewed.status_code, 200, renewed.text)
        self.assertEqual(renewed.json()["fulfilled"], True)
        self.assertEqual(renewed.json()["handle"], self.handle)
        self.assertAlmostEqual(self._featured() - start, 31 * DAY, delta=2)
        self.assertEqual([row["src"] for row in self._purchases()], ["FEATURED_LINK_MONTH", "FEATURED_LINK_MONTH"])

    def test_first_invoice_does_not_extend(self):
        sid = self._id("cs_first")
        sub = self._id("sub_first")
        session, expanded = self._session(sid, "AD_LINK_SECTION", subscription=sub, amount=9900)
        self.assertEqual(self._post_checkout(session, expanded, self._id("evt_first")).status_code, 200)
        until = self._sponsor("AD_LINK_SECTION")
        invoice_id = self._id("in_create")
        invoice = {
            "id": invoice_id,
            "object": "invoice",
            "billing_reason": "subscription_create",
            "amount_paid": 9900,
            "subscription": sub,
        }
        ignored = self._post_invoice(invoice, self._id("evt_create"))
        self.assertEqual(ignored.status_code, 200, ignored.text)
        self.assertTrue(ignored.json().get("ignored"))
        self.assertAlmostEqual(self._sponsor("AD_LINK_SECTION"), until, delta=0.001)
        lied = dict(invoice)
        lied["billing_reason"] = "subscription_cycle"
        again = self._post_invoice(lied, self._id("evt_create_lie"))
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json().get("duplicate"))
        self.assertAlmostEqual(self._sponsor("AD_LINK_SECTION"), until, delta=0.001)

    def test_replayed_event_and_duplicate_object_do_not_extend_twice(self):
        sid = self._id("cs_replay")
        session, expanded = self._session(sid, "FEATURED_LINK_WEEK", amount=900)
        event_id = self._id("evt_replay")
        first = self._post_checkout(session, expanded, event_id)
        self.assertEqual(first.status_code, 200, first.text)
        until = self._featured()
        replay = self._post_checkout(session, expanded, event_id)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json().get("duplicate"))
        self.assertAlmostEqual(self._featured(), until, delta=0.001)
        other = self._post_checkout(session, expanded, self._id("evt_replay_other"))
        self.assertTrue(other.json().get("duplicate"))
        self.assertAlmostEqual(self._featured(), until, delta=0.001)
        self.assertEqual(len(self._purchases()), 1)
        results = [row["result"] for row in self._audits()]
        self.assertEqual(results, ["ok", "duplicate"])

    def test_unknown_handle_needs_review_and_returns_200(self):
        sid = self._id("cs_unknown")
        session, expanded = self._session(sid, "FEATURED_LINK_WEEK", handle="@nosuchbot99")
        response = self._post_checkout(session, expanded, self._id("evt_unknown"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json().get("needs_review"))
        self.assertNotIn("handle", response.json())
        self.assertIsNone(self._featured())
        audit = self._audits()[0]
        self.assertEqual(audit["result"], "needs_review")
        self.assertEqual(audit["handle"], "nosuchbot99")
        self.assertEqual(self._purchases(), [])

    def test_invalid_handle_is_not_stored(self):
        samples = ["user@example.com", "a", "has space", "<script>", "@@ab"]
        for raw in samples:
            sid = self._id("cs_bad")
            session, expanded = self._session(sid, "AD_LINK_WEEK", handle=raw, amount=2900)
            response = self._post_checkout(session, expanded, self._id("evt_bad"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json().get("needs_review"))
            self.assertNotIn(raw, response.text)
            self.assertIsNone(self._sponsor("AD_LINK_WEEK"))
        for row in self._audits():
            self.assertIsNone(row["handle"])
            self.assertEqual(row["result"], "needs_review")
        stored = json.dumps(self._audits())
        self.assertNotIn("example.com", stored)
        self.assertNotIn("<script>", stored)
        self.assertEqual(self._purchases(), [])

    def test_bad_quantities_need_review(self):
        sid = self._id("cs_miss")
        session, expanded = self._session(sid, "AD_LINK_DAY", quantity=None)
        missing = self._post_checkout(session, expanded, self._id("evt_miss"))
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertTrue(missing.json().get("needs_review"))
        self.assertIsNone(self._sponsor("AD_LINK_DAY"))

        for quantity in (0, 400, "3", True):
            sid = self._id("cs_qty")
            session, expanded = self._session(sid, "AD_LINK_DAY", quantity=quantity)
            response = self._post_checkout(session, expanded, self._id("evt_qty"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json().get("needs_review"), quantity)
            self.assertIsNone(self._sponsor("AD_LINK_DAY"))
        self.assertEqual(self._purchases(), [])

    def test_unpaid_completion_does_not_block_a_later_success(self):
        sid = self._id("cs_delay")
        session, expanded = self._session(sid, "FEATURED_LINK_WEEK", paid=False)
        ignored = self._post_checkout(session, expanded, self._id("evt_unpaid"))
        self.assertTrue(ignored.json().get("ignored"))
        self.assertIsNone(self._featured())
        session["payment_status"] = "paid"
        expanded["payment_status"] = "paid"
        paid = self._post_checkout(
            session, expanded, self._id("evt_async"), "checkout.session.async_payment_succeeded"
        )
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertTrue(paid.json().get("fulfilled"))
        self.assertAlmostEqual(self._featured(), time.time() + 7 * DAY, delta=5)

    def test_failed_payment_does_not_extend(self):
        sid = self._id("cs_fail")
        session, expanded = self._session(sid, "AD_LINK_SECTION", subscription=self._id("sub_fail"))
        failed = self._post_checkout(
            session, expanded, self._id("evt_fail"), "checkout.session.async_payment_failed"
        )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json().get("status"), "failed")
        self.assertIsNone(self._sponsor("AD_LINK_SECTION"))
        self.assertIsNone(self._mapping(session["subscription"]))

    def test_invoice_metadata_fallback_and_subscription_retrieve(self):
        invoice = {
            "id": self._id("in_meta"),
            "object": "invoice",
            "billing_reason": "subscription_cycle",
            "amount_paid": 2900,
            "parent": {
                "subscription_details": {
                    "subscription": "sub_meta_parent_1",
                    "metadata": {"tier": "featured_link_month", "bothandle": "@ChefByte"},
                }
            },
        }
        response = self._post_invoice(invoice, self._id("evt_meta"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json().get("fulfilled"))
        self.assertAlmostEqual(self._featured(), time.time() + 31 * DAY, delta=5)

        c = app.db()
        c.execute("UPDATE bots SET featured_until=NULL WHERE handle=?", (self.handle,))
        c.commit()
        c.close()

        def retrieve_sub(sub_id):
            self.assertEqual(sub_id, "sub_remote_section_1")
            return {"id": sub_id, "metadata": {"tier": "AD_LINK_SECTION", "handle": "@ChefByte"}}

        remote = {
            "id": self._id("in_remote"),
            "object": "invoice",
            "billing_reason": "subscription_cycle",
            "amount_paid": 9900,
            "subscription": "sub_remote_section_1",
        }
        granted = self._post_invoice(remote, self._id("evt_remote"), subscription=retrieve_sub)
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()["tier"], "AD_LINK_SECTION")
        self.assertAlmostEqual(self._sponsor("AD_LINK_SECTION"), time.time() + 31 * DAY, delta=5)

    def test_renewal_after_expiry_starts_from_now(self):
        sub = self._id("sub_expired")
        c = app.db()
        past = time.time() - 10 * DAY
        c.execute("UPDATE bots SET featured_until=? WHERE handle=?", (past, self.handle))
        c.execute(
            "INSERT INTO stripe_subscriptions(subscription_id, handle, tier, created) VALUES(?,?,?,?)",
            (sub, self.handle, "FEATURED_LINK_MONTH", time.time()),
        )
        c.commit()
        c.close()
        invoice = {
            "id": self._id("in_expired"),
            "object": "invoice",
            "billing_reason": "subscription_cycle",
            "amount_paid": 2900,
            "subscription": sub,
        }
        response = self._post_invoice(invoice, self._id("evt_expired"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertAlmostEqual(self._featured(), time.time() + 31 * DAY, delta=5)
        self.assertGreater(self._featured(), past + 31 * DAY)

    def test_replayed_invoice_does_not_extend_twice(self):
        sub = self._id("sub_inv_replay")
        c = app.db()
        c.execute(
            "INSERT INTO stripe_subscriptions(subscription_id, handle, tier, created) VALUES(?,?,?,?)",
            (sub, self.handle, "AD_LINK_SECTION", time.time()),
        )
        c.commit()
        c.close()
        invoice = {
            "id": self._id("in_replay"),
            "object": "invoice",
            "billing_reason": "subscription_cycle",
            "amount_paid": 9900,
            "subscription": sub,
        }
        event_id = self._id("evt_inv_replay")
        first = self._post_invoice(invoice, event_id)
        self.assertTrue(first.json().get("fulfilled"))
        until = self._sponsor("AD_LINK_SECTION")
        replay = self._post_invoice(invoice, event_id)
        self.assertTrue(replay.json().get("duplicate"))
        other = self._post_invoice(invoice, self._id("evt_inv_other"))
        self.assertTrue(other.json().get("duplicate"))
        self.assertAlmostEqual(self._sponsor("AD_LINK_SECTION"), until, delta=0.001)
        self.assertEqual(len(self._purchases()), 1)

    def test_admin_fulfillment_auth_escape_and_json(self):
        missing = self.client.get("/admin/fulfillment")
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.get("/admin/fulfillment", headers={"Authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 401)
        c = app.db()
        c.execute("UPDATE admin_actors SET active=0, revoked=?", (time.time(),))
        c.commit()
        c.close()
        try:
            closed = self.client.get("/admin/fulfillment", headers=HDR)
            self.assertEqual(closed.status_code, 503, closed.text)
        finally:
            app.bootstrap_named_admin_keys(KEY, KEY_CEO)
        c = app.db()
        c.execute(
            """INSERT INTO fulfillment_audit(ts, event_id, object_id, tier, handle, action, result, amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            (time.time(), "evt_admin_plant", "<script>alert(1)</script>", '"><img src=x>', "chefbyte", "extend", "ok", 500),
        )
        c.commit()
        c.close()
        page = self.client.get("/admin/fulfillment", headers=HDR)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertEqual(page.headers["cache-control"], "private, no-store")
        self.assertEqual(page.headers["x-robots-tag"], "noindex")
        self.assertIn("&lt;script&gt;", page.text)
        self.assertNotIn("<script>", page.text)
        self.assertIn("&lt;img", page.text)
        self.assertIn("no persistent disk", page.text)
        body = self.client.get("/admin/fulfillment?format=json", headers=HDR)
        upper = self.client.get("/admin/fulfillment?format=JSON", headers=HDR)
        self.assertEqual(body.status_code, 200, body.text)
        self.assertIn("application/json", body.headers["content-type"])
        self.assertEqual(upper.json()["events"][0]["object_id"], "<script>alert(1)</script>")
        self.assertEqual(
            set(body.json()["events"][0]),
            {"id", "ts", "event_id", "object_id", "tier", "handle", "action", "result", "amount"},
        )
        self.assertIn("email", body.json()["note"].lower())

    def test_sponsor_pages_escape_with_text_content(self):
        for path in ("/ads", "/partners"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, page.text)
            match = re.search(r'<script id="sponsor-list">(.*?)</script>', page.text, re.S)
            self.assertIsNotNone(match, path)
            script = match.group(1)
            self.assertIn("textContent", script)
            self.assertIn("/api/sponsors", script)
            self.assertNotIn("innerHTML", script)
            self.assertIn('id="sponsors"', page.text)
        session, expanded = self._session(self._id("cs_show"), "AD_LINK_WEEK", amount=2900)
        self.assertEqual(
            self._post_checkout(session, expanded, self._id("evt_show")).status_code, 200
        )
        shown = self.client.get("/api/sponsors").json()["sponsors"]
        self.assertEqual(shown[0]["handle"], self.handle)
        c = app.db()
        c.execute("UPDATE sponsorships SET active_until=? WHERE handle=?", (time.time() - 5, self.handle))
        c.commit()
        c.close()
        self.assertEqual(self.client.get("/api/sponsors").json()["sponsors"], [])
        session, expanded = self._session(self._id("cs_ban"), "AD_LINK_WEEK", amount=2900)
        self.assertTrue(self._post_checkout(session, expanded, self._id("evt_ban")).json().get("fulfilled"))
        c = app.db()
        c.execute("UPDATE bots SET banned=1 WHERE handle=?", (self.handle,))
        c.commit()
        c.close()
        self.assertEqual(self.client.get("/api/sponsors").json()["sponsors"], [])

    def test_featured_purchase_is_not_listed_as_a_sponsor(self):
        session, expanded = self._session(self._id("cs_feat_only"), "FEATURED_LINK_WEEK")
        self.assertTrue(self._post_checkout(session, expanded, self._id("evt_feat_only")).json().get("fulfilled"))
        self.assertEqual(self.client.get("/api/sponsors").json()["sponsors"], [])

    def test_funnel_placement_src_does_not_replace_career_rules(self):
        self.assertEqual(funnel.clean_src("featured_link_week"), "FEATURED_LINK_WEEK")
        self.assertEqual(funnel.clean_src("card"), "card")
        self.assertIsNone(funnel.clean_src("<script>"))
        self.assertTrue(funnel.PLACEMENT_SOURCES.isdisjoint(funnel.SOURCES))
        self.assertEqual(funnel.PLACEMENT_SOURCES, frozenset(fulfillment.TIERS))
        self.assertTrue(funnel.record_event("purchase_complete", src="FEATURED_LINK_WEEK"))
        self.assertFalse(funnel.record_event("purchase_complete"))
        self.assertFalse(funnel.record_event("purchase_complete", src="card"))
        self.assertEqual(self._purchases(), [{"src": "FEATURED_LINK_WEEK", "report_id": None}])

    def test_career_report_checkout_is_unchanged(self):
        class Session:
            def __init__(self):
                self.id = "cs_test_career_keep"
                self.url = "https://checkout.stripe.com/c/pay/cs_test_career_keep"
                self.payment_status = "unpaid"
                self.metadata = {}
                self.client_reference_id = ""
                self.customer_details = {"email": "buyer@example.com"}

        session = Session()

        def stripe_create(**kwargs):
            session.metadata = dict(kwargs.get("metadata") or {})
            session.client_reference_id = kwargs.get("client_reference_id")
            return session

        def retrieve(sid):
            self.assertEqual(sid, session.id)
            return session

        with mock.patch("stripe.checkout.Session.create", stripe_create), mock.patch(
            "stripe.checkout.Session.retrieve", retrieve
        ):
            started = self.client.post("/api/reports/checkout", json=INTAKE)
            self.assertEqual(started.status_code, 200, started.text)
            session.payment_status = "paid"
            raw = json.dumps({
                "id": "evt_career_keep_1",
                "type": "checkout.session.completed",
                "data": {"object": {"id": session.id, "object": "checkout.session"}},
            }).encode()
            unlocked = self.client.post(
                "/api/stripe/webhook",
                content=raw,
                headers={"Stripe-Signature": _sign("whsec_test", raw), "Content-Type": "application/json"},
            )
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        self.assertTrue(unlocked.json().get("id"))
        self.assertNotIn("fulfilled", unlocked.json())
        self.assertIsNone(self._featured())
        self.assertEqual(self.client.get("/api/sponsors").json()["sponsors"], [])
