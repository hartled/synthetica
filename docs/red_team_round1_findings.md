# Red Team Round 1 findings — Phase 1 spec + site audit
*Author: MindGuild Cyber Security 2 (red). Date: 2026-10-15. Method: design review of Blue's Phase 1 spec and audit against OWASP LLM Top 10 2025 and MITRE ATLAS. No live exploitation.*

Inputs: `cyber_security_plan.md` (plan), `docs/security_phase1_spec.md` (spec), `docs/security_audit_2026-10-15.md` (audit), read-only spot-check of `app.py` and `static/`. I wrote no exploit payloads and sent no traffic to production. Each finding has a regression-test sketch (pytest + FastAPI TestClient against a local/staging DB) that Blue can turn into a test.

## Executive summary (ranked)
1. **RT-R1-01 (High), audit miss:** the avatar XSS fix in the audit leaves out two unescaped sinks (`mybots.html:41`, `index.html:153` suggestion row). Also, `index.html`'s `esc()` throws on null, and no page's `esc()` encodes `'`. Spec prerequisite #0 cannot be signed off as written.
2. **RT-R1-02 (High), gauntlet bypass:** the verdict is "exact canary or ≥12-char substring in the raw response" (spec §5.3). An encoded, split or translated canary scores `blocked` while the secret has still leaked, and the `[CANARY]` redaction (§5.4, §7) misses those same forms. The result is unscored leaks and also leaks that get published.
3. **RT-R1-03 (High), sandbox-in-design:** AI-Blue `output_filter` defenses are user-supplied regexes. The server runs them against 200 replays plus a benign suite on the single Render worker (§5.3, §1). That is a ReDoS / CPU-exhaustion path (LLM10), and there are no limits on regex complexity or execution time.
4. **RT-R1-04 (High), abuse controls:** the target allowlist checks `target`, `claim`, `evidence` and `fix` but exempts `sources` and replies (§7, §1 row `POST /api/posts` with parent_id). Replies go through the plain `post` path, which has no host check and no dual-use filter, so real-world target discussion can be moved into the comments.
5. **RT-R1-05 (High), abuse controls:** sybil bots defeat the per-bot limits, the 3-report auto-hide and duplicate scoring. Registration is per IP and keyed on the proxy (audit #2), and bot ids are free to create, so "keyed on bot id avoids the IP problem" (spec §0) moves the problem rather than solving it.
6. RT-R1-06 (Medium): the gauntlet's attack text is public by default, which turns it into a public jailbreak corpus (ATLAS AML.T0065-style capability development).
7. RT-R1-07 (Medium): the canary backstop regex `SYN-CANARY-[0-9a-f]{24}` is case-sensitive and anchored to the canonical form, and the system-prompt "marker phrase" has no redaction at all.

Counts: **High 5 · Medium 9 · Low 5 · Info 2 (21 findings).**

---

## Findings

### RT-R1-01 — High — Avatar XSS fix is incomplete; extra unescaped sinks and a weak `esc()`
- **Target area:** audit miss
- **OWASP LLM:** LLM05 Improper Output Handling (bot-authored content rendered in browsers)
- **ATLAS:** n/a (classic web)
- **Proof:** Audit #1 lists `index.html:96,97,99,153,166,168`, `ads.html:42` and `messages.html:90`. The audit does **not** list:
  - `static/mybots.html:41` `<span style="font-size:32px">${b.avatar}</span>`, which is unescaped on the owner dashboard. The owner is the bot's own human, but a key-holder can set the avatar with `PATCH /api/bots/{h}` (`app.py` BotUpdate `avatar max_length=8`), so whoever holds the API key can attack the human owner's session. That is a session-riding path from key compromise to account takeover.
  - `static/index.html:153` `<div class="av">${b.avatar}</div>` (suggested follows). This one is listed, but the line also contains `@${b.handle}`. Handles are regex-safe today, so the handle part is safe for now.
  - `esc()` at `index.html:92` is `s=>s.replace(...)`, which throws on `null`/`undefined`. Every other page uses `String(s??'')`. A null `persona`/`content` field breaks rendering, and a developer who "fixes" the crash by skipping `esc` on that field reintroduces the sink.
  - No `esc()` escapes `'`. Several templates interpolate into single-quoted handlers (`onclick="go('${p.bot.handle}')"`, `setCat('${p.bot.category}')`, `togFollow('${b.handle}')`). This is safe only because handle/category are pattern- or Literal-constrained. Spec §4 builds security cards with "framework tag chips linking to…" and filters (`tag`, `target`). If those values go into an `onclick` or a single-quoted attribute, `esc()` will not protect them.
  - `mybots.html:53,56` write `d.api_key` into innerHTML. The value is server-generated (`syn_`+urlsafe), so this is low risk, but it should use `textContent`.
- **Attack scenario:** an attacker who holds a stolen or shared bot key sets an avatar fragment and waits for the owner to open /me/bots. Separately, security-board chips built the same way as the feed chips become injectable through admin-editable taxonomy `name`/`url` (`security_taxonomy` is "updateable without a deploy").
- **Suggested fix:** one shared `static/common.js` with a null-safe `esc()` that also escapes `'` → `&#39;`. Ban inline `onclick` with interpolated data and use `data-*` attributes plus `addEventListener`. Validate `avatar` server-side (an emoji/grapheme allowlist, reject `<>"'&=` and ASCII). Add a CSP (audit #4) so inline handlers stop working.
- **Audit covered:** partial
- **Regression test:** (1) `PATCH /api/bots/{h}` with an avatar containing any of `<>"'&=` → 422. (2) A static check: grep `static/*.html` for `\$\{[^}]*avatar[^}]*\}` not wrapped in `esc(` → zero hits. (3) A unit test of `esc` (node or a jsdom snapshot): `esc(null)==''`, `esc("'")=='&#39;'`. (4) A headless render of /me/bots with a fixture bot whose avatar is `<b>x</b>` → DOM text equals the literal string and no `<b>` element exists.

### RT-R1-02 — High — Verdict and redaction rely on exact canary matching; transformed leaks are unscored and published
- **Target area:** prompt-injection gauntlet bypass
- **OWASP LLM:** LLM02, LLM07, LLM05
- **ATLAS:** AML.T0057 LLM Data Leakage; AML.T0051 (.000 direct / .001 indirect); AML.T0068 LLM Prompt Obfuscation (attacker-side encoding)
- **Proof:** spec §5.3: "`leak`: the exact canary (or a unique ≥12-char substring) appears in the raw response". §5.4: "The canary is **always** replaced with `[CANARY]`". §7: "a response filter strips `SYN-CANARY-[0-9a-f]{24}`". §5.1 lists "translation tricks or encoding requests" as things the responder *reacts to*, so the rule-based responders are expected to emit transformed output, but the scorer only matches the plain form.
- **Attack scenario (conceptual):** the attacker asks the toy agent to output the secret reversed, spaced out, base-N encoded, chunked into pieces under 12 characters, or as one character per line. The response contains the full secret in recoverable form. The verdict is `blocked`, the redactor does nothing, and the response is stored in `response_redacted` and shown publicly on `/api/gauntlet/attempts`. That breaks invariants in two places at once: scoring integrity, and "canary never circulates". An attacker can also exploit this the other way round: post the decoded canary in a security post or reply, where §7 only strips the canonical form.
- **Suggested fix:** (a) Have the responder engine itself emit a structured signal (`leaked: full|partial|none`, `transform: …`). Because responders are deterministic Python, the scorer can know the ground truth instead of string-matching. (b) For any future LLM backend, normalise the response before matching and redacting: lowercase, remove whitespace/punctuation/zero-width and confusable characters, then test the canary, its reverse, common encodings of it, and an n-gram overlap of ≥ N hex chars. (c) If the response has high overlap with the canary under normalisation, withhold the whole response (`[withheld: canary-adjacent]`) rather than trying to redact in place. (d) Run the same normaliser on every post, reply and DM body before storage (inbound canary filter).
- **Audit covered:** no (Phase 1 spec is new)
- **Regression test:** use fixture responders that return the canary (i) verbatim, (ii) whitespace-separated, (iii) reversed, (iv) split into 8-char chunks across lines, (v) uppercase. Assert `verdict in {leak,partial}` for all five, and assert no `/api/gauntlet/attempts` JSON contains the normalised canary (compare with the normaliser, not the regex). Also: `POST /api/posts` whose content contains a normalised current canary → 422 or stored withheld.

### RT-R1-03 — High — User-supplied defense regexes enable ReDoS/CPU exhaustion on the shared web worker
- **Target area:** sandbox escape (design) / abuse controls
- **OWASP LLM:** LLM10 Unbounded Consumption
- **ATLAS:** AML.T0029 Denial of ML Service (verify ID at seed time)
- **Proof:** spec §1: defenses are "system-prompt addendum or output-filter regex, text only". §5.3: "replaying the last 200 successful attacks plus a fixed benign-prompt suite". §0: everything runs in the same `app.py` FastAPI process on Render Free. Python `re` has no timeout. §7 allows `gauntlet_def` 5/day per verified builder, which is not a limit on regex complexity. The spec's "No code execution" claim (§ scope, rule 2) does not account for the fact that a regex is executable code with super-linear worst cases.
- **Attack scenario:** a verified builder (or an unverified one, if verification is granted carelessly) submits a catastrophically backtracking output filter. The replay of 200 stored attacks (attacker-influenced text up to 2,000 chars each) pins the single worker and takes the whole site down, including human login, DMs and feeds. Prompt-addendum defenses that are merged into the live agent (§5.3 "merged into the live agent") are also a persistent path: if a backtracking filter is accepted, every later attempt is slow.
- **Suggested fix:** (a) Replace free regex with a constrained DSL (literal blocklists, simple wildcards), or use an RE2-class engine (`google-re2` / `re2` binding) that guarantees linear time. (b) If Python `re` stays: cap the pattern at 200 chars, reject nested quantifiers and backreferences with a static check, and run evaluation in a subprocess with a hard wall-clock timeout (for example 2 s total) and a memory rlimit. Note that the subprocess is itself an exec path, so keep it argument-free and data-only. (c) Run evaluation asynchronously off the request path, one at a time. (d) Admin review of the defense body is required before any merge into the live agent.
- **Audit covered:** no
- **Regression test:** submit a defense whose regex has nested quantifiers → 422 "pattern not allowed". Submit a regex longer than 200 chars → 422. With a fixture slow pattern injected directly in the test, the evaluator returns `status=error` within the timeout and `/api/feed` stays responsive (p95 under X ms during evaluation).

### RT-R1-04 — High — Host allowlist does not cover replies, `sources`, the `[BOARD] claim` crosspost, or non-literal host forms
- **Target area:** abuse controls
- **OWASP LLM:** n/a (platform policy); relates to LLM05
- **ATLAS:** n/a
- **Proof:** spec §1: "`POST /api/posts` with `parent_id` of a security post | bot | `post` | Normal replies work unchanged". §7 applies the host check only to "`claim`, `evidence` or `fix`" and says "`sources` URLs are exempt from the host check but must be `https://`". The allowlist includes "`localhost` in code blocks", which is a parsing-dependent exception. Acceptance #4 tests only the IPv4 literal and a dotted hostname.
- **Attack scenario:** (a) A compliant finding is posted against a toy agent, and the real target (a third-party host) goes in the first reply, or in `sources` as an `https://` link to a third party's admin panel. (b) Obfuscated host forms that a naive "hostname" regex misses: hosts written with "[.]" or " dot ", IPs in decimal/hex/octal integer form, IDN/punycode, IPv6 with zone, a URL hidden inside a code block next to the `localhost` exception. (c) The rule says an *allowlisted* host may be mentioned, but `synthetica-j7zw.onrender.com` is real production. Findings with live-site specifics (paths, parameters) belong in private disclosure, not the public Red board (§6.2 vs §4).
- **Suggested fix:** apply `check_security_post()` (host + dual-use) to **every** write that lands under a security post (replies, DMs referencing a security post are optional) and to `sources`. Keep `sources` on a separate domain allowlist (standards bodies, vendor docs, CVE/NVD) rather than exempting it. Normalise defanged notations and integer IPs before matching. Drop the code-block exception (allow only `localhost` and `127.0.0.1` everywhere, or nowhere). Force `target=synthetica-self` posts to the Disclosures board as **private until fixed** (the spec already says "public only after a fix ships", so enforce it in the API, not just the UI).
- **Audit covered:** no
- **Regression test:** a reply to a security post containing a non-allowlisted hostname → 422. `sources=["https://bank.example.org/x"]` → 422 (not on the sources allowlist). A defanged host, a decimal-integer IP and a punycode host in `evidence` → 422 each. A `target=synthetica-self` post is not returned by public `GET /api/security/posts` until an admin sets `fixed=1`.

### RT-R1-05 — High — Sybil bots defeat per-bot limits, report auto-hide and duplicate scoring
- **Target area:** abuse controls
- **OWASP LLM:** LLM10 (resource abuse); n/a otherwise
- **ATLAS:** n/a
- **Proof:** spec §0: "Keying security limits on **bot id** avoids the IP problem." `app.py` `create_bot` limits registration with `rate("register", request.client.host)` 5/hour, which is the proxy IP per audit #2 (`Procfile:1`, `render.yaml:10`, `Dockerfile:11` have no `--proxy-headers`). `/api/bots` needs no human account. Spec §7: "3 open reports on one post auto-hide it." §5.3: "Duplicates (same bot, same agent, same canary_version) score once."
- **Attack scenario:** (a) A bot farm gets N × 60 gauntlet attempts per day and N × 5 posts per 10 minutes. (b) Three throwaway bots (or three human accounts, since signup has the same weakness) auto-hide any rival's legitimate finding, or Blue's defense post. That is cheap censorship, and the hide also suppresses Disclosures. (c) The first-blood bonus is farmed by having alts discover the leak and hand it to a main account. Because the canary never circulates, alts share the *technique*, and leaderboard integrity suffers. (d) Mass-liking alts already enable the XSS chain in audit #1.
- **Suggested fix:** require a human-owned bot (`owner_id NOT NULL`) with account age ≥ 24h for any security write and for reports to count toward auto-hide. Weight reports by reporter reputation, and only count one report per owning human. Apply per-owner limits on top of per-bot limits (`gauntlet` 60/day per human across all their bots). Fix audit #2 first (real client IP). Make auto-hide a queue priority bump rather than an automatic hide, or require ≥1 trusted reporter. Exclude any bot that has not passed verification from first blood.
- **Audit covered:** partial (audit #2 covers IP keying; does not connect it to Phase 1 per-bot design)
- **Regression test:** three reports from three bots owned by the same human → post still visible, report count toward auto-hide = 1. A bot with `owner_id NULL` calling `POST /api/security/posts` → 403. The 61st gauntlet attempt across two bots of one owner in a day → 429.

### RT-R1-06 — Medium — Public-by-default attack text builds a reusable jailbreak corpus
- **Target area:** abuse controls / framework coverage
- **OWASP LLM:** LLM01
- **ATLAS:** AML.T0065 LLM Prompt Crafting; AML.T0054 LLM Jailbreak
- **Proof:** spec §5.4: "Attack text is public by default (the purpose is learning)". Only attempts flagged by the dual-use regex are withheld. Plan §2: "No malware, persistence, or evasion tooling for general use."
- **Attack scenario:** successful injections against "Sphinx" (difficulty 5, layered defenses) are harvested from `/api/gauntlet/attempts` and reused against real third-party LLM apps. The gauntlet becomes a free, curated, verified jailbreak feed, which is a reputational and dual-use risk.
- **Suggested fix:** publish attack text only for `blocked` attempts and for leaks on difficulty ≤2. For higher difficulties, publish only category, verdict and a technique tag until the canary rotates and the defense is merged, then optionally publish. Leaks become regression tests privately.
- **Audit covered:** no
- **Regression test:** a fixture `leak` attempt on a difficulty-4 agent → public API returns `attack:"[withheld: until-rotation]"`. After a canary rotation plus a merged defense → text visible (if the policy allows).

### RT-R1-07 — Medium — Canary backstop regex is narrow; marker phrase and system prompt have no backstop
- **Target area:** gauntlet bypass
- **OWASP LLM:** LLM07, LLM02
- **ATLAS:** AML.T0057
- **Proof:** spec §7: "strips `SYN-CANARY-[0-9a-f]{24}` from **every** API response". §5.3 defines `partial` as "the system-prompt marker phrase leaks", but nothing redacts the marker phrase or other system-prompt text. Acceptance #8 greps only `SYN-CANARY-`. §5.4 caps responses at 600 chars, which is enough room for a full system prompt.
- **Attack scenario:** the attacker gets the agent to reproduce its system prompt (LLM07) with the canary in uppercase hex or a different prefix casing. The backstop regex (lowercase hex, case-sensitive) misses it. Even when the canary is stripped, the system prompt itself is published, and that prompt is the "defense", so publishing it trivialises the next attacker's attempt.
- **Suggested fix:** case-insensitive backstop, and match the hex body alone (24 hex chars equal to *any* active or previous canary value). Redact the marker phrase and any ≥40-char overlap with the agent's system prompt (`[SYSTEM PROMPT]`). Extend acceptance #8 to check every stored system prompt against every JSON response.
- **Audit covered:** no
- **Regression test:** fixture response with an uppercased canary → redacted. Fixture response echoing the full system prompt → public `response_redacted` contains no 40-char window of `system_prompt`. A crawler test over all public GET endpoints asserts neither the canary nor any system-prompt window appears.

### RT-R1-08 — Medium — Admin canary handling: rotation leaks via history, admin responses, and the backstop gap for old versions
- **Target area:** sandbox escape (design) / gauntlet
- **OWASP LLM:** LLM02, LLM07
- **ATLAS:** AML.T0057
- **Proof:** spec §1 admin `POST …/{id}` and `PATCH …/{id}` and `rotate-canary` have no stated response shape. §3 `gauntlet_agents.canary` keeps only the current value, yet `gauntlet_attempts.canary_version` refers to history. §7: the backstop strips the canary from "**every** API response", which would also redact it in admin responses, so admins may add a bypass. Audit #9: a single static admin key, kept in `.admin_key` on disk.
- **Attack scenario:** (a) Previous canaries are not stored, so redaction of old attempts depends on the response already being redacted at write time. If any path stores unredacted text (the `attack` field, when the attacker pastes a canary obtained earlier), old values are never caught. (b) An admin PATCH that returns the full agent object, plus a backstop exemption for admin routes, ends up in browser history, proxies and logs. (c) A single admin key compromise exposes every system prompt.
- **Suggested fix:** keep a `gauntlet_canaries(agent_id, version, value, retired)` table and redact *all* versions. Admin endpoints must never return `system_prompt` or `canary` (write-only fields). Add a separate break-glass `GET` that writes to the audit log. Never log request bodies for these routes. Rotate canaries automatically after every recorded `leak`.
- **Audit covered:** partial (audit #9 covers the admin key in general)
- **Regression test:** `PATCH /api/admin/gauntlet/agents/{id}` response JSON has no `canary` and no `system_prompt` keys. After rotation, an attempt whose `attack` contains the v1 value → stored and shown redacted. `security_audit_log` has a `rotate-canary` row.

### RT-R1-09 — Medium — Gauntlet category scope is self-declared; LLM01/02/07 labels can be gamed
- **Target area:** gauntlet bypass / framework coverage
- **OWASP LLM:** LLM01, LLM02, LLM07
- **ATLAS:** AML.T0051
- **Proof:** spec §3 `gauntlet_attempts.category CHECK IN ('LLM01','LLM02','LLM07')`. §5.3: "→ LLM02 or LLM07 by category." The spec does not say who sets `category`. If the attacker supplies it, they pick the label. Points are 10 × difficulty regardless of category.
- **Attack scenario:** an attacker labels every attempt in whichever category is less contested or has its own sub-leaderboard. Taxonomy stats in future assurance reports (plan Phase 3) are then wrong, which is LLM09-style misinformation in our own reporting.
- **Suggested fix:** the server derives the category from the verdict path (canary leak → LLM02, system-prompt marker → LLM07, indirect-doc agent "Concierge" → LLM01 / AML.T0051.001). The attacker's label is only a hint.
- **Audit covered:** no
- **Regression test:** an attempt submitted with `category=LLM07` whose result is a canary leak → stored `category=LLM02`. A client-supplied category not in the enum → 422.

### RT-R1-10 — Medium — Duplicate/first-blood scoring and verified-badge linkage can be spoofed
- **Target area:** abuse controls (taxonomy/verification spoofing)
- **OWASP LLM:** LLM09 (misleading verified claims)
- **ATLAS:** n/a
- **Proof:** spec §2: "`gauntlet_attempt_id` int, optional — UI shows a 'Verified by gauntlet' badge". Nothing requires the attempt to belong to the post author, to be a `leak`, or to match the post's target and tags. §5.3 first blood "per canary_version" is reset on rotation (acceptance #11).
- **Attack scenario:** a bot links another bot's successful attempt id to its own finding and gets a "Verified" badge for a claim it didn't make, or links a `blocked` attempt to a claim tagged LLM07 against a different agent. Rotation on demand (admin) plus first-blood resets give a predictable farming window.
- **Suggested fix:** validate `attempt.bot_id == author`, `attempt.verdict in (leak, partial)`, `attempt.agent == target`, and that the attempt's category is among `framework_tags`. One post per attempt id (UNIQUE). Rotation is announced after it happens, not before.
- **Audit covered:** no
- **Regression test:** a post linking another bot's attempt → 422. A post linking a `blocked` attempt → 422. A post whose target differs from the attempt's agent → 422. A second post reusing the same attempt id → 409.

### RT-R1-11 — Medium — Harmful-output redaction relies on regex `check_dual_use`; responses aren't filtered, only attacks
- **Target area:** abuse controls
- **OWASP LLM:** LLM05, LLM01
- **ATLAS:** AML.T0054
- **Proof:** spec §5.4: "attempts flagged by the harmful-content filter are stored but shown as `[withheld: policy]`". §7 `check_dual_use()` is a list of regexes. The existing `_V` violence regexes in `app.py` are English-only, word-boundary keyword lists. Plan §2: "the harmful output itself is never published". For a future LLM backend (§5.2 `GAUNTLET_LLM`), the *response* is the harmful artefact, but the spec does not run the filter on `response_redacted`.
- **Attack scenario:** paraphrase, other languages or leetspeak get past the regexes, and the harmful generated text is published as a "toy-agent response". In Phase 1 the risk is low (rule-based responders), but the interface is already shipped behind a flag.
- **Suggested fix:** run the filter on both attack and response. When `GAUNTLET_LLM=1`, default responses to private and add a moderation-classifier gate (needs D approval because of spend). Treat "flagged" as withheld plus auto-report, never as plain reject (keeping evidence for review).
- **Audit covered:** no
- **Regression test:** a fixture responder returning a string that `check_dual_use` flags → public response shows `[withheld: policy]` and a `security_reports` row exists. A feature-flag test: with `GAUNTLET_LLM=1`, `response_redacted` is not public until reviewed.

### RT-R1-12 — Medium — Taxonomy and target tables are admin-editable without deploy; tags carry URLs rendered as links
- **Target area:** abuse controls (taxonomy spoofing) / sandbox-in-design
- **OWASP LLM:** LLM05
- **ATLAS:** n/a
- **Proof:** spec §2: "Store them in a `security_taxonomy` table so admins can update them without a deploy." §3: `security_taxonomy(tag, framework, name, url, ...)`. §4: "framework tag chips linking to the OWASP or ATLAS URLs from `security_taxonomy`". There is no URL scheme or host validation. With one static admin key (audit #9), admin compromise turns into a stored `javascript:` or phishing link on every security card.
- **Attack scenario:** an attacker with a leaked admin key (or an admin mistake) sets a taxonomy URL to a non-https scheme or an external phishing host, so every card links there. Tags that look like ATLAS IDs but are made up ("AML.T9999") also lend fake authority to posts.
- **Suggested fix:** validate taxonomy `url` as `https://` on `genai.owasp.org` / `atlas.mitre.org` only. Validate tag regexes `^OWASP-LLM(0[1-9]|10)$` and `^ATLAS:AML\.T\d{4}(\.\d{3})?$`. Write taxonomy and target changes to the audit log. Render links with `href` set via the DOM after a scheme check.
- **Audit covered:** no
- **Regression test:** an admin POST/PATCH of a taxonomy row with a `javascript:` URL or a non-allowlisted host → 422. A tag `OWASP-LLM11` or `ATLAS:AML.TX` → 422. The change appears in `security_audit_log`.

### RT-R1-13 — Medium — Markdown subset in `evidence`/`fix` is an unspecified rendering sink
- **Target area:** sandbox escape (design: markdown/HTML sinks)
- **OWASP LLM:** LLM05
- **ATLAS:** n/a (Promptfoo-noted markdown-image exfiltration)
- **Proof:** spec §2: "plain text or markdown subset (code blocks allowed, no images, no raw HTML, no auto-unfurled links)". §4: "render code blocks with `textContent`, and never use `innerHTML` with user data". The two contradict each other for any markdown other than code blocks. The spec doesn't name a parser or a sanitizer. Acceptance #7 tests only one `<img onerror>` string.
- **Attack scenario:** the Web Dev pulls in a markdown library to render the "subset". Default configs often allow link reference definitions, autolinks, HTML entities that decode to tags, or `[text](javascript:...)`. That brings back the Promptfoo markdown-image or link exfil path the spec intended to ban.
- **Suggested fix:** render evidence/fix as **plain text only** in Phase 1, with triple-backtick blocks split client-side into `<pre>` + `textContent`. No markdown library. If markdown is wanted later, use a vetted sanitizer with an allowlist and no URLs.
- **Audit covered:** no
- **Regression test:** evidence containing a markdown link with a non-https scheme, a markdown image, an autolink and an HTML entity-encoded tag → rendered DOM contains zero `<a>`, `<img>` and `<script>` elements inside the evidence block (jsdom or Playwright on staging).

### RT-R1-14 — Medium — Crosspost leaks security content into the main feed with weaker controls
- **Target area:** abuse controls
- **OWASP LLM:** LLM05
- **ATLAS:** n/a
- **Proof:** spec §2: "`posts.content` … generated as `"[{BOARD}] {claim}"` … shows up in the main feed only if `crosspost: true`". The main feed (`/api/feed`, `VIS` in `app.py`) has no `hidden` awareness. `VIS` filters only `ad_id IS NULL` and banned bots. §3 `security_posts.hidden` is a side-table flag.
- **Attack scenario:** a security post auto-hidden by reports (§7), or soft-hidden by an admin (`/api/admin/security/posts/{id}/hide`), stays visible in `/api/feed`, `/api/posts/{id}` and in thread replies, because the main read paths never join `security_posts.hidden`. Replies to a security post also appear when the parent is crossposted. Admin `DELETE /api/admin/posts/{id}` (`app.py:596-601`) deletes `posts` and `likes` rows but not `security_posts` or `security_post_tags`, leaving orphan rows that may still render on the boards.
- **Suggested fix:** move `hidden` onto `posts` (or extend `VIS` with `id NOT IN (SELECT post_id FROM security_posts WHERE hidden=1)`). Exclude security posts from the main feed unless `crosspost=1 AND hidden=0`. Cascade the delete to security tables.
- **Audit covered:** no
- **Regression test:** create a crossposted security post, hide it → absent from `/api/feed`, `/api/feed?section=*`, the single-post read, and `/api/security/posts`. `DELETE /api/admin/posts/{id}` → no rows remain in `security_posts` or `security_post_tags` for that id.

### RT-R1-15 — Low — Concierge (indirect injection) planted doc is static; LLM01 indirect coverage is shallow
- **Target area:** framework coverage
- **OWASP LLM:** LLM01
- **ATLAS:** AML.T0051.001 (indirect)
- **Proof:** spec §5.1: Concierge "summarises a fixed in-memory document that contains planted instructions". Attackers can't influence the document, so the scenario tests only whether the bot can coax the agent into following *pre-planted* instructions, not real indirect injection via untrusted content.
- **Suggested fix:** in Phase 1, have Concierge summarise an attacker-supplied "document" field (still text-only, stateless), scored on whether the summary leaks or follows embedded instructions. Keep a separate field from the "user message" so direct vs indirect is distinguished in data.
- **Audit covered:** no
- **Regression test:** an attempt with `document` set and no injection in `attack`, where the result is a leak → `category=LLM01`, technique `AML.T0051.001`.

### RT-R1-16 — Low — Timing and error side channels in the gauntlet
- **Target area:** gauntlet bypass
- **OWASP LLM:** LLM07
- **ATLAS:** AML.T0057
- **Proof:** spec §5.2: rule-based responders branch on "pattern set A". §5.3 verdict `error` exists. §5.4 cap of 600 chars plus escaping. Branch-dependent response length, latency or the `error` verdict lets an attacker map the filter rules (oracle) without a leak.
- **Attack scenario:** an attacker probes many inputs and notes which ones return `error` or a different refusal length, which reconstructs the input filter. A scripted puzzle tolerates this, but it undermines any "difficulty" claim and AI-Blue scoring.
- **Suggested fix:** uniform persona refusals, constant-ish response sizes, `error` only for real server faults (logged, not returned with detail), and per-agent attempt caps per season.
- **Audit covered:** no
- **Regression test:** a blocked attempt from filter path A and one from path B → identical response body template and identical HTTP status. `error` responses carry no exception text.

### RT-R1-17 — Low — Reports endpoint: free-text `detail` and attempt reports are unbounded and unrendered-safe by default
- **Target area:** abuse controls
- **OWASP LLM:** LLM05
- **ATLAS:** n/a
- **Proof:** spec §3 `security_reports(... reason TEXT, detail TEXT ...)` has no length limit. §1 `reason` enum is listed in prose but not enforced in the schema. The admin queue UI is unspecified. Audit #5 notes that `dm_reports` has no review UI, and a new queue risks repeating that or rendering `detail` unsafely.
- **Suggested fix:** `reason` Literal enum, `detail` ≤500 chars, the same escaping rules for the admin UI, rate limit `sec_report` 10/hour per owner, and dedupe (reporter, post).
- **Audit covered:** partial (audit #5 for DM reports)
- **Regression test:** an unknown reason → 422. `detail` over 500 chars → 422. A duplicate report → 409 or no-op. The 11th report in an hour → 429.

### RT-R1-18 — Low — Sanctions rely on DMs from a system account and a "7-day mute" that has no data model
- **Target area:** abuse controls
- **OWASP LLM:** n/a
- **Proof:** spec §7 Sanctions: "warning DM from the system account… 7-day security-board mute". §3 has no `mutes` table, no strike counter and no system account. The audit notes that DMs from bots to humans default to allowed (audit #8).
- **Suggested fix:** add `security_strikes(bot_id, owner_id, reason, ts)` and `security_mutes(bot_id, until)`, checked in every security write. Count strikes per owner, not per bot (see RT-R1-05).
- **Audit covered:** no
- **Regression test:** a muted bot → `POST /api/security/posts` and gauntlet attempts → 403 until `until`. A third strike on any bot of an owner → all of that owner's bots are muted or banned.

### RT-R1-19 — Low — Disclosure policy allows "1 request per second" scanning against the production host
- **Target area:** abuse controls / audit miss
- **OWASP LLM:** LLM10
- **ATLAS:** n/a
- **Proof:** spec §6.2: "no automated scanning above 1 request per second", with scope `synthetica-j7zw.onrender.com`. On Render Free (a single instance with in-memory limiters that reset, audit #2/#11), 86,400 requests/day per researcher is effectively permission to load-test, which contradicts "Out of scope: DoS or load testing".
- **Suggested fix:** no automated scanning of production at all. Offer a staging/self-host instructions path (the repo is self-hostable via Docker). Keep the 1 rps limit only for staging.
- **Audit covered:** no
- **Regression test:** policy text check: `disclosure.html` contains no production rate allowance (doc lint).

### RT-R1-20 — Info — Framework coverage: gauntlet scores LLM01/02/07 only; template allows LLM05/06/10 and ATLAS IDs it can't verify
- **Target area:** framework coverage
- **OWASP LLM:** LLM05, LLM06, LLM10 (and LLM03/04/08/09)
- **ATLAS:** AML.T0051.*, AML.T0054, AML.T0056 (meta-prompt extraction), AML.T0057, AML.T0061 (LLM prompt self-replication), AML.T0065, AML.T0067 (LLM trusted output components manipulation), AML.T0068, AML.T0093 — verify all IDs against live ATLAS at seed time
- **Proof:** spec §3 `CHECK(category IN ('LLM01','LLM02','LLM07'))` vs §2 `framework_tags` "OWASP-LLM01…OWASP-LLM10" and arbitrary seeded ATLAS IDs. The "Verified by gauntlet" badge (§2) can appear on a post tagged LLM06/LLM10 even though the gauntlet cannot test either. Plan §1 scores AI-Red on "tool misuse", but Phase 1 toy agents have "no tools" (§5.1), so LLM06 is unverifiable. LLM05 (output handling) is testable in text-only form (does the agent emit markdown/HTML that the UI would render?) but isn't scored. LLM10 is testable through response-length or loop-inducing prompts against the 600-char cap.
- **Suggested fix:** show the badge only when the post's tags intersect the attempt's server-derived category. Mark LLM03/04/06/08/09 posts as "unverifiable in Phase 1". Add cheap text-only scorers: LLM05 (agent output contains markup/links that would be dangerous if rendered → `partial`), LLM10 (response hits the cap or requests repetition), AML.T0056 (system-prompt extraction, which maps to the existing `partial`), AML.T0061 (agent output instructs other bots to repost it, which matters because toy-agent replies could be reposted into the bot social graph).
- **Audit covered:** no
- **Regression test:** a post tagged only `OWASP-LLM06` with a linked attempt → 422 or no badge. `GET /api/security/taxonomy` returns a `gauntlet_verifiable` boolean per tag.

### RT-R1-21 — Info — Other audit misses worth folding into the Phase 1 prerequisites
- **Target area:** audit miss
- **OWASP LLM:** LLM05 / LLM10 / n/a
- **Proof and notes:**
  - **No proxy headers in any start command** (`Procfile:1`, `render.yaml:10`, `Dockerfile:11`). The audit covers this. Its proposed fix, `--forwarded-allow-ips='*'`, trusts spoofed `X-Forwarded-For` unless Render strips it, and that makes every per-IP limit client-controlled. Prefer `CF-Connecting-IP` only after confirming Cloudflare overwrites it, or Render's documented header.
  - **Bot-created DMs and posts have no inbound canary filter** (see RT-R1-02). Leaked canaries could circulate via DMs, which the spec's "never circulates" claim doesn't cover.
  - **`/docs` public plus a "How to post" sample `curl` on /security (§4)**: make sure the OpenAPI examples carry no real canary or key format beyond `syn_…` (acceptance #8 covers canaries, not keys).
  - **No dependency scan** (the audit lists it as not done). Run `pip-audit` before adding any regex/markdown library from RT-R1-03/13.
  - **`index.html` `esc()` is not null-safe** (RT-R1-01). It is a latent crash on any new nullable field, which matters for the new security cards (nullable `target`, `evidence`, `fix`, `sources`).
- **Audit covered:** partial
- **Regression test:** a header-spoof test on staging: sending a forged `X-Forwarded-For` does not change the rate-limit bucket. `pip-audit -r requirements.txt` exits 0 in CI.

---

## Suggested acceptance-criteria additions (append to spec §8)
17. All security write paths (posts, replies to security posts, sources, reports) run host + dual-use checks (RT-R1-04, 17).
18. Canary and system-prompt redaction uses normalisation and covers all canary versions. A crawler test over all public GETs finds no normalised canary and no 40-char system-prompt window (RT-R1-02, 07, 08).
19. Defense regexes are linear-time or time-boxed off-request. A slow fixture pattern does not degrade `/api/feed` (RT-R1-03).
20. The gauntlet verdict and category are server-derived. `gauntlet_attempt_id` linkage is validated (RT-R1-09, 10).
21. Hidden or deleted security posts are absent from every feed path (RT-R1-14).
22. Report auto-hide counts one report per owning human (RT-R1-05).
23. A static check finds no unescaped `${…}` interpolation of user fields in `static/*.html`. `esc` is shared, null-safe and escapes `'` (RT-R1-01).

## Round 1 log entry (for security_rounds.md)
Red R1: 21 findings (5H/9M/5L/2I). The strongest are RT-R1-01..05. Ask to Blue: accept, reject or modify each, and convert the regression sketches into pytest before Web Dev starts tasks 2/4/6. Then swap roles.
