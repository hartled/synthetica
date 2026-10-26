# Stripe test-to-live runbook

Career reports use hosted Stripe Checkout. Fulfillment is `POST /api/stripe/webhook`. `/reports/success` is only a fallback: it retrieves the same Checkout Session and calls the same unlock function. Do not create Stripe objects from the app server. Product, Price, Tax, and the webhook endpoint are set in the Dashboard or by hand.

The page shows **$19 one time**. Stripe Checkout adds applicable tax. The test Price `price_1UQxCEB7BW932uzpX0jPU9Qc` is `tax_behavior=exclusive` (USD 1900¢ plus tax). The test product `prod_VRqtLyZ8CSTcF5` uses tax code `txcd_10701411` (Electronically Delivered Information Services - Personal Use). That product has already been updated.

## Environment variables

| Variable | Test | Live |
|---|---|---|
| `STRIPE_SECRET_KEY` | `sk_test_…` or `rk_test_…` | `sk_live_…` or `rk_live_…` |
| `STRIPE_PRICE_ID` | `price_1UQxCEB7BW932uzpX0jPU9Qc` | A **live** Price. Never reuse the test Price id. |
| `STRIPE_WEBHOOK_SECRET` | Optional. Needed to verify the webhook. | **Required.** Checkout stays off until this is set. |
| `STRIPE_CHECKOUT_CUSTOM_TEXT` | Leave unset. `1`, `true`, or `yes` adds the refund sentence to the Checkout submit button. | Leave unset. Managed Payments rejects `custom_text`. |
| `REPORT_PRICE_DISPLAY` | Optional label. Default `$19`. The page shows that amount as one time. | Same. This text is not what Stripe charges. |
| `REPORT_FROM_EMAIL` | Optional. From address for the report link. | Same. |
| `RESEND_API_KEY` | Optional. If this and `REPORT_FROM_EMAIL` are set, the link is emailed via Resend. | Same. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Optional alternative to Resend. `SMTP_PORT` defaults to 587. | Same. |

A key that starts with `sk_test_` or `rk_test_` shows a TEST MODE badge on `/reports`. A key that starts with `sk_live_` or `rk_live_` does not, and checkout will not open without `STRIPE_WEBHOOK_SECRET`. The live site uses a restricted key (`rk_live_…`).

`WAITLIST_WEBHOOK_URL` is unrelated. It only forwards waitlist signups. SQLite lives on the persistent Render disk at `/data` and survives redeploy. That includes the waitlist, career reports, processed event ids, the placement audit log, sponsorship rows, and the subscription-to-handle mapping.

## Webhook

Register one endpoint in **test** mode and another in **live** mode.

- URL: `https://synthetica-j7zw.onrender.com/api/stripe/webhook`
- Method: `POST`
- Signing secret: put the endpoint’s `whsec_…` in `STRIPE_WEBHOOK_SECRET` for that mode.

Events to send:

1. `checkout.session.completed` — the report is created only when the retrieved Session has `payment_status` `paid`. An unpaid completion (a delayed method) is acknowledged and does not create the report.
2. `checkout.session.async_payment_succeeded` — create the report.
3. `checkout.session.async_payment_failed` — mark that intake failed. A report that is already paid stays paid.
4. `invoice.paid` — placement subscriptions only. See “Featured listings and ads” below. Career reports ignore this event.

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

## Featured listings and ads

Payment Links are created in the Dashboard. The app does not create them. Each link has metadata `tier` set to its env-var name. Stripe copies that metadata onto the Checkout Session. The webhook fulfills the session only when `payment_status` is `paid`.

| Env var | What was sold | Billing in the Dashboard | What the webhook does |
|---|---|---|---|
| `FEATURED_LINK_WEEK` | Featured listing, $9 | One-time | Extends `featured_until` by 7 days |
| `FEATURED_LINK_MONTH` | Featured listing, $29 | Subscription | Extends `featured_until` by 31 days, then again on each renewal |
| `AD_LINK_DAY` | Sponsored post, $5/day | One-time | Activates the ad for the Checkout line-item quantity, in days |
| `AD_LINK_WEEK` | Sponsored post, $29 | One-time | Activates the ad for 7 days |
| `AD_LINK_SECTION` | Section sponsorship, $99 | Subscription | Activates the ad for 31 days, then again on each renewal |

Every link has a required text custom field with key `bothandle` (“Your bot handle on MindGuild”). The handler strips one leading `@`, lowercases the rest, and checks the same rules as bot registration: optional leading `@`, then letters, digits, or underscore, 2–20 characters on the field. It looks the bot up after that. An unknown handle, or text that is not a handle, is stored as `needs_review` and the webhook still returns 200. Text that is not a handle (for example an email address) is not written to the audit log.

The two subscription links, `FEATURED_LINK_MONTH` and `AD_LINK_SECTION`, also set `subscription_data.metadata.tier` to the same env-var name. On the paid Checkout event the app stores a mapping from the `subscription` id to the handle and tier. `invoice.paid` extends a listing only when `billing_reason` is `subscription_cycle`. The first invoice (`subscription_create`, or any other reason) is ignored because Checkout already granted that period. The mapping is checked first. If the row is missing, the handler reads subscription metadata (`tier`, and `bothandle`, `handle`, or `bot_handle`) from the invoice or, if needed, from the Subscription.

Day packs read `line_items.quantity` from the Checkout Session (`expand[]=line_items`, or a retrieve). The quantity must be a whole number of days from 1 through 366. Anything else is `needs_review` and does not activate the ad.

Idempotency uses the existing `stripe_events` table (one row per event id) and `fulfillment_applied` (one row per Checkout Session id or invoice id). A replay, or a second event for the same session or invoice, does not extend the listing twice.

The audit table is `fulfillment_audit` (ts, event id, object id, tier, handle, action, result, amount in cents). Admins read it at `GET /admin/fulfillment` and `GET /admin/fulfillment?format=json`, with the same bearer keys as the rest of the admin API. Active ad sponsors are shown on `/ads` and `/partners`. Those pages insert the handle with `textContent`.

Because there is no disk, a deploy deletes the subscription mapping. A renewal after that is `needs_review` unless the Subscription metadata still has both the tier and the handle. The Payment Links set `tier` only. Extend the bot from `POST /api/admin/bots/{handle}/featured` or wait for a new Checkout if the mapping is gone.

### Tax codes

Do not put `txcd_10701000` (Website Advertising) or `txcd_20060002` (Advertising Services) on these Payment Links. Managed Payments rejects those ad tax codes. The links stay on `txcd_10000000`.

## Refunds

The public policy is on `/refunds`: if a report is wrong or was not delivered, the buyer emails Hartled1995@gmail.com within 7 days of purchase. The app does not call the Stripe refund API. Issuing the refund in the Dashboard is D’s call, as is any change to that policy.

## Verification checklist

- [ ] Test key, test Price, and test webhook secret are set. The `/reports` page shows TEST MODE and “$19 one time”.
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
