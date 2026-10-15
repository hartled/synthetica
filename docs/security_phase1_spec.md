# MindGuild security Phase 1 — constraints already accepted

Phase 1 boards, the gauntlet, and `posts.hidden` are not built yet. This file
records two constraints the regression suite checks now, so later spec text
cannot walk them back.

## Evidence and fix

Evidence and fix fields are **plain text only** (no markdown library). The
client renders them as text. There is no markdown parser in Phase 1.

## Disclosure

Researchers do no automated scanning of production at all. Exercise a local
or staging copy. Load testing production is out of scope.
