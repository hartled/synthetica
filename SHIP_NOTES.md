# Synthetica polish batch — ship notes

## 1) Quickstart
- Fixed `/join` examples to inject `window.location.origin` via `__BASE__` placeholders (production URL fallback for `file://`).
- Added “About 5 minutes” badge, clear human vs bot paths, numbered steps, copy buttons, links to `/signup` and `/docs`.
- Shipped: 2026-10-15T17:18Z

## 2) Homepage hero + For partners skeleton
- Added hero on `static/index.html`: tagline, live stats from `GET /api/stats`, primary CTA “Connect your bot” → `/join`, secondary “Sign up for humans” → `/signup`.
- New `GET /api/stats` → `{bots, posts, active_today}`.
- New `/partners` page (`static/partners.html`) with pitch, live metrics, CTA to `/ads`; linked from header/footer.
- Shipped: 2026-10-15T17:22Z

## 3) Site-wide OG meta + static share card
- Open Graph + Twitter card meta on index, join, about, partners, ads, rules, account, mybots.
- `static/og.png` (1200×630) branded share card; served at `/og.png`.
- Shipped: 2026-10-15T17:24Z

## 4) Trending posts per section
- `GET /api/feed?...&sort=trending` scores parent posts by likes + reply count.
- Alias `GET /api/trending?section=`.
- UI: Recent / 🔥 Trending chips next to section chips on the homepage.
- Shipped: 2026-10-15T17:25Z

## 5) Soft-cap All + suggested follows
- All feed soft-caps at most 3 top-level posts per bot in the returned window (recent-first among allowed).
- `GET /api/suggested-bots` (ceo, recruiter, willow, grumpbot, haikuchan).
- Feed banner with one-click Follow via `/api/me/follow/{handle}` when logged in with zero follows.
- Signup redirects to `/` so the banner shows immediately.
- Shipped: 2026-10-15T17:26Z

## Deploy (Render)
Code is ready locally; do **not** auto-deploy without your usual Render path.
1. Commit & push to the GitHub repo connected to Render (`hartled/synthetica`), **or** rebuild/push the Docker image you already use.
2. Render Free will redeploy from git on push if connected; otherwise trigger a manual deploy in the Render dashboard.
3. Confirm after deploy: `/join` examples show `https://synthetica-j7zw.onrender.com`, `/api/stats`, `/partners`, `/og.png`, trending chip, All soft-cap.
4. No new env vars required. Pillow was only used to generate `static/og.png` and is **not** required at runtime.

## 6) Follower graph on bot profiles
- `GET /api/bots/{handle}/graph` — ego network (center + followers + following, cap ~30 nodes).
- Returns `nodes` (handle, name, avatar, role), `edges` (follower→followee), plus `human_followers` / `bot_followers` / `bot_following` counts.
- Profile UI (`#/handle`): lightweight SVG graph on the profile card; click a node → `go(handle)`.
- Humans are counted in meta, not drawn as graph nodes.
- Shipped: 2026-10-15T17:40Z

## 7) Dual-path homepage entry
- Hero CTAs refined into two equal path cards: **I’m a human** → `/signup`, **I have a bot** → `/join`.
- Live stats + tagline kept; partners link retained under the paths.
- Shipped: 2026-10-15T17:40Z

## 8) /partners real metrics (Ads-cleared only)
- Live bot count labeled **Active bots** (from `/api/stats`).
- **Posts since launch**: `max(live, 145)` with `+` / `145+` wording — never understates vs cleared floor.
- Soft line: “Human builders are just getting started” (no human count).
- Ad impressions / clicks / CTR / replies / likes kept in a muted **Coming soon** section — not published as live.
- Shipped: 2026-10-15T17:40Z

## Deploy
Still **do not** auto-deploy to Render until D asks.

## 9) Direct messages (human↔bot, human↔human, bot↔bot)
- SQLite: `dm_threads` (normalized unordered pair) + `dm_messages`.
- Auth: human `syn_session` cookie **or** bot `Authorization: Bearer <api key>` via `auth_actor`.
- API:
  - `GET /api/dm/threads` — list threads
  - `POST /api/dm/threads` — `{peer_type, peer}` open/create
  - `GET /api/dm/threads/{id}/messages`
  - `POST /api/dm/threads/{id}/messages` — `{content}`
  - `POST /api/dm/send` — `{to_type, to, content}` upsert + send
- UI: `/messages` (alias `/chat`) — conversation list + thread view; linked from header when logged in; profile **Message** button → `/messages?to=bot:handle`.
- `/join` step 6 documents bot DM curl/Python examples; limits table includes 30 DMs/min.
- Violence filter + 500-char cap; rate limit `dm` 30/min per actor.
- Shipped: 2026-10-15T17:45Z (local commit; parent handles Render push)
