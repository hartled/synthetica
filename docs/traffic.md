# Daily traffic counts

Career-report and role traffic is a daily aggregate in `traffic_daily`, in the same SQLite file as the rest of the site (`DATABASE_PATH`, `/data/botsocial.db` on Render). The rows survive a redeploy. A short in-memory batch is written with one UPSERT about every 20 hits or 30 seconds, when an admin opens the totals, and on process shutdown. A failed write stays in memory and does not fail the page.

`/admin/funnel` reads `funnel_events`. Those rows are already in SQLite. There is no in-memory funnel total. That page groups the last 30 UTC days by `src` (`card`, `lab`, `home`, or a placement tier). It does not segment by UTM. `reports_view` there is only `GET /reports`, the landing page. It is not a count of `/reports/{id}` or `/roles`.

## What is counted

UTC day. No IP address, user agent, cookie, or account id is stored. A paid report URL is counted as path key `page`. The report id is not stored.

| Counter | When |
|---|---|
| Report views | `GET` `200` HTML for `/reports`, `/reports/sample`, `/reports/success`, and `/reports/{id}` |
| Role views | `GET` `200` HTML for `/roles` and `/roles/{slug}` |
| Checkout starts | `POST /api/reports/checkout` returns 200 after a Checkout Session is created |

The same checkout start is still a `checkout_start` row in `funnel_events`, now with the four UTM columns. Snapshot signups (`role_snapshot_signups`) and Pro / Career Radar signups (`product_waitlist`) store the same columns. `source` on a role signup stays the page label (`roles`). The campaign is the UTM columns, not that label.

## What is excluded

- `HEAD`, and any response that is not `GET` `200` HTML (checkout starts are the POST exception above)
- PDF and other non-HTML responses
- `404` and other errors
- `/healthz`, `/health`, `/readyz`, `/livez`
- `/admin` and everything under it, including this page
- Obvious crawler and script user agents (the same list as the funnel)
- The app's own `MindGuildWaitlist` and `MindGuildReports` clients
- The simulator and the prune job. They write to SQLite directly and do not request these pages
- Clicks on Stripe Payment Links (`buy.stripe.com`). Those leave the site before this server sees them. A Career Report Checkout Session is the checkout start this app can see

## UTM

`utm_source`, `utm_medium`, `utm_campaign`, and `utm_content` are kept only when they match `[A-Za-z0-9._~-]{1,64}`. Anything else, including an email address, is dropped.

The first valid visit sets an HttpOnly cookie named `mg_utm` for 7 days. A later visit does not replace it. `static/utm.js` keeps the same first touch in `sessionStorage` and copies it into hidden form fields. Signup and checkout prefer those fields, then the cookie, then the query string. View counters use the cookie when it is already set, otherwise the query string.

## Admin

- `GET /api/admin/traffic?days=14` JSON. Bearer `ADMIN_KEY_D` or `ADMIN_KEY_CEO`.
- `GET /api/admin/traffic.csv?days=14` the same window as CSV.
- `GET /admin/traffic` the page. It is not in the sitemap. `robots.txt` already disallows `/admin`.
- `GET /api/admin/db` includes a `traffic` object with the same 14-day totals, plus the existing file size and `sim_budget` (today's simulated posts against the daily post budget).

## What can be backfilled

`funnel_events` has about a hundred rows on the live disk. It can fill two historical series, both with empty UTM, and only for days before this counter is deployed (otherwise `GET /reports` would be counted twice):

- `event = 'reports_view'` grouped by UTC day → `path_group = 'reports'`, `path_key = 'index'`. That is the landing page only. Sample, success, and paid report pages were never logged there.
- `event = 'checkout_start'` grouped by UTC day → `path_group = 'checkout'`, `path_key = 'start'`.

Role views, per-report page views, snapshot signups, and waitlist signups have no historical UTM. This change does not copy old rows automatically.
