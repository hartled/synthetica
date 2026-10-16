# Stripe test-to-live runbook

Career reports use hosted Stripe Checkout. Fulfillment is `POST /api/stripe/webhook`. `/reports/success` is only a fallback: it retrieves the same Checkout Session and calls the same unlock function. Do not create Stripe objects from the app server. Product, Price, Tax, and the webhook endpoint are set in the Dashboard or by hand.

The displayed price is **$19 + applicable tax**. The test Price `price_1UQxCEB7BW932uzpX0jPU9Qc` is `tax_behavior=exclusive` (USD 1900¢ plus tax). The test product `prod_VRqtLyZ8CSTcF5` uses tax code `txcd_10701411` (Electronically Delivered Information Services - Personal Use). That product has already been updated.

## Environment variables

| Variable | Test | Live |
|---|---|---|
| `STRIPE_SECRET_KEY` | `sk_test_…` or `rk_test_…` | `sk_live_…` or `rk_live_…` |
| `STRIPE_PRICE_ID` | `price_1UQxCEB7BW932uzpX0jPU9Qc` | A **live** Price. Never reuse the test Price id. |
| `STRIPE_WEBHOOK_SECRET` | Optional. Needed to verify the webhook. | **Required.** Checkout stays off until this is set. |
| `STRIPE_CHECKOUT_CUSTOM_TEXT` | Leave unset. `1`, `true`, or `yes` adds the refund sentence to the Checkout submit button. | Leave unset. Managed Payments rejects `custom_text`. |
| `REPORT_PRICE_DISPLAY` | Optional label. Default `$19`. The page adds “+ applicable tax”. | Same. This text is not what Stripe charges. |
| `REPORT_FROM_EMAIL` | Optional. From address for the report link. | Same. |
| `RESEND_API_KEY` | Optional. If this and `REPORT_FROM_EMAIL` are set, the link is emailed via Resend. | Same. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Optional alternative to Resend. `SMTP_PORT` defaults to 587. | Same. |

A key that starts with `sk_test_` or `rk_test_` shows a TEST MODE badge on `/reports`. A key that starts with `sk_live_` or `rk_live_` does not, and checkout will not open without `STRIPE_WEBHOOK_SECRET`. The live site uses a restricted key (`rk_live_…`).

`WAITLIST_WEBHOOK_URL` is unrelated. It only forwards waitlist signups. Render Free has no persistent disk, so SQLite (waitlist, reports, and processed event ids) is wiped on deploy.

## Webhook

Register one endpoint in **test** mode and another in **live** mode.

- URL: `https://synthetica-j7zw.onrender.com/api/stripe/webhook`
- Method: `POST`
- Signing secret: put the endpoint’s `whsec_…` in `STRIPE_WEBHOOK_SECRET` for that mode.

Events to send:

1. `checkout.session.completed` — the report is created only when the retrieved Session has `payment_status` `paid`. An unpaid completion (a delayed method) is acknowledged and does not create the report.
2. `checkout.session.async_payment_succeeded` — create the report.
3. `checkout.session.async_payment_failed` — mark that intake failed. A report that is already paid stays paid.

The handler verifies `Stripe-Signature` with `STRIPE_WEBHOOK_SECRET`. It stores each event id and ignores a replay. The old path `POST /api/reports/stripe/webhook` runs the same handler. Register the `/api/stripe/webhook` URL.

Checkout Session creation sends an `Idempotency-Key` derived from the pending report id (`mindguild-report-` plus that id), turns on `automatic_tax`, and requires a billing address. `custom_text` is included only when `STRIPE_CHECKOUT_CUSTOM_TEXT` is set. The `/refunds` links on `/reports`, the success page, and the footer stay in the HTML either way.

## Stripe Tax

Tax calculation is on in code (`automatic_tax.enabled` and `billing_address_collection=required`). D still has to finish Stripe Tax in the Dashboard before live mode:

- Set the origin address.
- Add the tax registrations you will collect.
- Confirm Tax shows as active.

The live product must use that same tax code, `txcd_10701411` (Electronically Delivered Information Services - Personal Use). The live Price must keep `tax_behavior=exclusive`, same as the test Price. The test product `prod_VRqtLyZ8CSTcF5` has already been updated. Checkout fails closed if Tax is not activated: Session creation errors and the buyer sees “Checkout is unavailable.” Fix the Dashboard setup. Do not turn `automatic_tax` off to force a charge through.

## Managed Payments

The live Stripe account has Managed Payments on, and D is keeping it on. Stripe rejects a Checkout Session that sends `custom_text` while Managed Payments is enabled (`custom_text cannot be used with Managed Payments`). Session creation therefore omits `custom_text` unless `STRIPE_CHECKOUT_CUSTOM_TEXT` is `1`, `true`, or `yes`. The default is off. The app does not send `managed_payments[enabled]=false`. Buyers still reach the refund policy from `/reports`, the success page, and the site footer.

A failed Session create logs the exception class and Stripe’s `user_message`, or `code` when `user_message` is empty. The log line does not include the API key.

## Refunds

The public policy is on `/refunds`: if a report is wrong or was not delivered, the buyer emails Hartled1995@gmail.com within 7 days of purchase. The app does not call the Stripe refund API. Issuing the refund in the Dashboard is D’s call, as is any change to that policy.

## Verification checklist

- [ ] Test key, test Price, and test webhook secret are set. The `/reports` page shows TEST MODE and “$19 + applicable tax”.
- [ ] Pay with `4242…`. The webhook creates the report even if the browser never opens `/reports/success`.
- [ ] Opening `/reports/success?session_id=…` shows **Open your report**. A second visit does not create a second report.
- [ ] An unpaid `checkout.session.completed` does not unlock the report. `checkout.session.async_payment_succeeded` does.
- [ ] `checkout.session.async_payment_failed` leaves the report unavailable.
- [ ] Replaying the same event id does not generate the report again.
- [ ] `/refunds` is linked from `/reports`, the success page, and the site footer.
- [ ] Stripe Tax is active, with origin address and registrations. A test payment shows tax separately from the $19 when a rate applies.
- [ ] Only then: live product with tax code `txcd_10701411`, live Price with `tax_behavior=exclusive`, `sk_live_…` or `rk_live_…`, and the **live** webhook secret. Leave `STRIPE_CHECKOUT_CUSTOM_TEXT` unset while Managed Payments is on. Do not point a live key at `price_1UQxCEB7BW932uzpX0jPU9Qc`.

## Rollback

Unset `STRIPE_SECRET_KEY` or `STRIPE_PRICE_ID`. Checkout returns to “Coming soon. Join the waitlist.” Unset `STRIPE_WEBHOOK_SECRET` on a live key and checkout turns off as well. Already delivered report URLs keep working until the next deploy wipes SQLite. Disable the Dashboard webhook endpoint if you do not want Stripe to keep retrying.
