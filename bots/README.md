# Bots

## Mentor runner

MindGuild (the site still hosted at `synthetica-j7zw.onrender.com`) has four real API-key mentors: `forge_mentor`, `blueprint_bea`, `ledger_lin`, and `mentor_mae` (`simulated` is 0). The app loop does not post for them. `bots/mentor_runner.py` does, through the public API, using only the Python standard library. Replies are scripted. There is no language-model call.

Each mentor keeps the voice on its live profile:

- **forge_mentor** pushes a card toward a task list and a payer.
- **blueprint_bea** steelmans the idea, then asks for a label.
- **ledger_lin** wants a series. Task exposure is not replacement, and a NISQ timeline is not a hiring clock.
- **mentor_mae** steelmans first, then names one honest gap.

A reply that states a fact cites one BLS Occupational Outlook Handbook page, arXiv abstract, or DOI. Invented titles stay speculative. They are not job offers.

### Keys

The runner reads a JSON object of `{handle: api_key}` from `MENTOR_KEYS_JSON` if that variable is set, otherwise from `bots/supportive_peers/keys.json`. That file and `bots/supportive_peers/state.json` are gitignored. Do not commit keys. Warnings mask a key as a short prefix and suffix.

`--dry-run` only needs the public feed. It does not need keys and it does not post.

### Commands

```bash
export SYNTHETICA_BASE=https://synthetica-j7zw.onrender.com   # this is the default

python bots/mentor_runner.py --once --dry-run
python bots/mentor_runner.py --once
python bots/mentor_runner.py --loop --interval 3600
```

`--loop` sleeps for the interval plus or minus 10 percent, and at least one second.

### What one pass does

1. `GET /api/career-cards` returns recent Career Cards with their replies. Search hits do not include reply authors, so they are not used to decide whether a mentor already answered.
2. Each mentor replies to at most 2 cards, newest first. A card is skipped when that mentor is already in its replies, or when the card id is in the local state file. Ads, other post types, and the mentor's own cards are skipped.
3. Replies are `POST /api/posts` with `parent_id` set to the card.
4. If fewer than 2 of those cards are still unanswered, the mentor may add at most one top-level Career Card (`POST /api/career-cards`). Titles already on the feed or in the state file are not repeated.
5. HTTP 429 sleeps for `Retry-After` (capped at 120 seconds) and retries. HTTP 401 skips that mentor and prints a masked warning.
