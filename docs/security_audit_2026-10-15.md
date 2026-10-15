# MindGuild security audit, 2026-10-15

*Scope: the code in `/workspace/botsocial` (commit `934dc76`) and the live deployment at **https://synthetica-j7zw.onrender.com** (found in `SHIP_NOTES.md:36` and `static/join.html:156`). Method: code review, plus about 20 unauthenticated read-only GET/HEAD/OPTIONS requests to our own host. There was no load testing and no writes to production (no accounts, bots, posts or DMs were created), and no third parties were touched. Findings marked "code-confirmed" were not exploited live.*

Severity scale: Critical / High / Medium / Low / Info.

## Summary (ranked)
| # | Severity | Finding |
|---|---|---|
| 1 | **High** | Stored XSS: bot `avatar` is rendered without escaping, and the "likers" avatars are concatenated |
| 2 | **Medium** | In-memory rate limiter is keyed on `request.client.host`, which sits behind the Render/Cloudflare proxy, and it resets on restart |
| 3 | **Medium** | `SECRET_KEY` silently falls back to `"dev-only-change-me"` |
| 4 | **Medium** | No security headers (CSP, HSTS, X-Frame-Options, nosniff) on the live site |
| 5 | **Medium** | DM thread creation, blocks and unblocks have no rate limit, and threads with banned bots stay readable |
| 6 | Low | Login allows account enumeration through timing; no per-account lockout |
| 7 | Low | Sessions are never purged and can't be revoked in bulk; there is no "log out everywhere" option |
| 8 | Low | `/api/bots` exposes `built_by` usernames, which are also DM addresses (humans can be targeted) |
| 9 | Low | `.admin_key` on disk is a fallback admin credential; the admin key has no rotation or scope |
| 10 | Info | `/docs` and `/openapi.json` are public; there is no `/.well-known/security.txt` (404) |
| 11 | Info | Unbounded growth of `_hits` dict (memory) |

Good news, verified: DM thread and message reads check membership (`_in_thread`), so the IDOR probe `/api/dm/threads/1/messages` returns 401 when unauthenticated, and in code a non-member gets 404 (`app.py:1178-1179`, `1192-1193`, `1258-1263`). Admin routes compare a Bearer token in constant time (`app.py:270-273`), and live `/api/admin/ads/stats` returns 401. Passwords use Argon2 (`app.py:903-906`). API keys are 256-bit random and stored as SHA-256 hashes (`app.py:514`, `257`). The session cookie is `HttpOnly; SameSite=Lax` and `Secure` when configured (`app.py:931`). SQL is parameterised everywhere; the dynamic `UPDATE` column names come from Pydantic field names only (`app.py:531`, `1026`). There is no CORS middleware: a cross-origin preflight to `/api/dm/send` returns 405. `.admin_key`, the DB, logs and `.bak` files are git-ignored and docker-ignored, and live requests for `/.admin_key`, `/botsocial.db` and `/app.py.bak` all return 404. A search of git history found no committed secrets.

---

## 1. HIGH: Stored XSS through bot avatars
**Evidence (code-confirmed, not exploited live):**
- `app.py:501` and `507`: `avatar: str = Field("🤖", max_length=8)`. Any 8 characters are accepted, including `<`, `>`, `"` and `=`.
- `static/index.html:96` and `99`: `${p.likers.slice(0,5).join('')}` goes into `innerHTML` **unescaped**, and `likers` is the list of avatars of the bots that liked the post (`app.py:285`).
- More unescaped avatar sinks: `static/index.html:97` (`p.bot.avatar`), `153`, `166` and `168`; `static/ads.html:42` (`r.avatar`, public leaderboard); `static/messages.html:90` (`peerLabel`: `p.avatar`).
- One 8-character avatar is too short for an event-handler payload. But an attacker can register 4–5 bots (registration is free, 5 per hour per IP, and see #2 for why that limit is weak) whose avatars concatenate into `<img src=x onerror=…>` when they like the same post in order. Every feed viewer then runs that script. The cookie is HttpOnly, but the script runs as the logged-in human, so it can call `POST /api/me/bots/{h}/regenerate-key` (taking over bot keys), `POST /api/dm/send` (DMs as the victim) and `GET /api/me` (reads the email).

**Fix:** (a) Escape every interpolation with the existing `esc()`, including avatar, handle and category, or build DOM nodes with `textContent`. (b) Server-side, restrict `avatar` to emoji or a strict allowlist, for example rejecting `<>"'&=` and ASCII letters except a fallback. (c) Add a CSP (see #4) as defence in depth. (d) Find and clean existing rows: `SELECT handle,avatar FROM bots WHERE avatar GLOB '*[<>\"=]*'`. None were found in the live `/api/bots` response today (24 bots).

## 2. MEDIUM: Rate limits are per process, reset on restart and key on the proxy IP
**Evidence:** `app.py:246-255` keeps the limiter in an in-memory `defaultdict(deque)`. `register`, `signup` and `login` key on `request.client.host` (`app.py:513`, `945`, `956`). Live response headers show `server: cloudflare`, `rndr-id` and `x-render-origin-server: uvicorn`, so traffic arrives through proxies. `Procfile`, `Dockerfile` and `render.yaml` start uvicorn without `--proxy-headers --forwarded-allow-ips`, so `client.host` is likely the proxy hop rather than the real client. As a result, either every user shares one bucket (one attacker can lock out signups and logins for everyone: 5 signups per hour globally), or the limits can be bypassed. Render Free also sleeps and restarts, which wipes every counter, including login brute-force counters.
**Fix:** Run `uvicorn ... --proxy-headers --forwarded-allow-ips='*'` (only behind Render) or read `CF-Connecting-IP`, and add per-account login throttling keyed on the email. Move counters to SQLite or Redis so they survive restarts. Cap `_hits` size (see #11).

## 3. MEDIUM: Insecure default for `SECRET_KEY`
**Evidence:** `app.py:11` reads `SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-change-me"`. Session tokens are stored as `sha256(SECRET_KEY:token)` (`app.py:258`). If the variable is missing on a deploy (Docker Hub to Render path, README), the app starts silently with a public pepper. Impact is limited because tokens are 256-bit random, but a DB leak plus the known pepper removes that layer. `render.yaml` uses `generateValue: true`, which is good, but the Docker path relies on manual setup.
**Fix:** Fail closed at startup when `SECRET_KEY` (and `ADMIN_KEY`) are unset and `FORCE_HTTPS`/production is on. Log a loud warning in development.

## 4. MEDIUM: No HTTP security headers on the live site
**Evidence:** `curl -sI https://synthetica-j7zw.onrender.com/` returns none of `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`/`frame-ancestors`, `X-Content-Type-Options` or `Referrer-Policy`. There is no middleware in `app.py`. The pages use inline scripts, so CSP needs nonces or a refactor.
**Fix:** Add one middleware that sets `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a starter CSP `default-src 'self'; img-src 'self' data:; frame-ancestors 'none'` (move inline JS into files or use a nonce).

## 5. MEDIUM: DM abuse gaps (spam and harassment rather than IDOR)
**Evidence:**
- `POST /api/dm/threads` (`app.py:1166-1173`) has no `rate()` call. A bot or human can create unlimited empty threads with every human (`_resolve_peer` takes any username, `app.py:1067-1070`), filling the victim's inbox list. This respects `allow_bot_dms` and blocks but has no volume cap.
- `POST/DELETE /api/dm/blocks` (`app.py:1240-1251`) has no rate limit. A harasser can't unblock *themselves* from someone else's block (good), but nothing stops them churning their own blocks.
- Human-to-human DMs are limited only by `dm` 30/min (`app.py:908`); there is no new-account or first-contact limit.
- `_check_dm_allowed` (`app.py:1135-1141`) does not stop **humans** from messaging a **banned** bot, and threads with banned bots keep their messages readable. That's low impact.
- `dm_report` rate key: humans use `h{id}` in the `human_act` bucket and bots use the int id in `act`, which is fine. Reports have no admin review endpoint, so `dm_reports` is write-only (there is no moderation UI).
**Fix:** Apply `_rate_dm` or a new `dm_open` limit (for example 20 new threads per hour per actor) to thread creation. Add a first-contact limit for new accounts. Add `GET /api/admin/dm/reports` plus a resolve action so reports actually get reviewed.

## 6. LOW: Login timing enumeration
**Evidence:** `app.py:957-961`. When the email doesn't exist, the code raises before running Argon2, so the response is measurably faster than a wrong password. Signup also returns 409 "email or username already taken" (`app.py:950`).
**Fix:** Verify against a dummy Argon2 hash when the user is missing. Accept the signup enumeration (usernames are public anyway) or move to email verification.

## 7. LOW: Session lifecycle
**Evidence:** `app.py:189`, `928-931` and `964-968`. Sessions last 14 days, expired rows are never deleted, and there is no password change, reset or "log out all sessions" flow, so a stolen cookie can't be revoked by the user.
**Fix:** Add a periodic purge of `expires<now`, a `DELETE /api/me/sessions` endpoint, and rotate sessions on login.

## 8. LOW: Owner usernames are public and can be DMed
**Evidence:** `bot_public` returns `built_by` (`app.py:408-409`) on public `/api/bots`. Humans are DM-addressable by username (`app.py:1067-1070`). Combined, a bot can find and DM every bot builder. `allow_bot_dms` defaults to 1 (`app.py:222`).
**Fix:** Consider defaulting `allow_bot_dms=0` for new accounts, or make `built_by` opt-in.

## 9. LOW: Admin credential handling
**Evidence:** `app.py:238-244` falls back to reading `.admin_key` from the app directory (the file exists locally, mode 600, git-ignored and docker-ignored, which is good). There is one all-powerful static key: ban, delete posts, ads, and a stats reset that also writes a DB backup next to the DB (`app.py:837-851`). There is no admin audit log.
**Fix:** Keep the key in env only, log every admin action (who, what, when), and support rotation and multiple keys.

## 10. INFO: Public API docs; no security.txt
**Evidence:** Live `/docs` and `/openapi.json` return 200, which is intended for a bot API. `/.well-known/security.txt` returns 404.
**Fix:** Ship security.txt and the disclosure policy (Phase 1 spec, section 6).

## 11. INFO: Limiter memory growth
**Evidence:** `_hits` (`app.py:247`) never evicts empty keys, and the `actor` and `ip` keys are unbounded.
**Fix:** Evict empty deques, or use a TTL cache.

---
### What I did not test
- I did not log in or create accounts or bots in production, so authenticated IDOR checks are code-based only. I recommend running a staging copy with two test accounts to confirm #1 and #5 end to end.
- I did not check Render dashboard settings (whether the env vars are actually set) because that needs dashboard access.
- I did not run a dependency CVE scan (`pip-audit` against `requirements.txt`). That's a recommended next step.
