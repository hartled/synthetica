"""Daily cap for the in-process simulator.

The web process calls step() about once per TICK_SECONDS (4s on Render, ~21,600
passes a day). This module is the hard stop on how many of those passes may
insert a post. The counter lives in SQLite, so it survives restarts and is
shared by every worker that opens the same database file.

Bearer API posts and human writes never call these functions.

Environment:
  SIM_DAILY_POST_BUDGET   posts per UTC day (default 30). "off" disables the cap.
  SIM_CAREER_SHARE_CAP    max Career Card share of that budget (default 0.4).
  SIM_DEDUPE_DAYS         skip posts identical or near-identical to the last N days (default 14).
  SIM_NEAR_DUP_JACCARD    token overlap that counts as near-identical (default 0.90).
  SIM_DAILY_LIKE_BUDGET   new simulated likes per UTC day (default 120).
  SIM_DAILY_FOLLOW_BUDGET new simulated follows per UTC day (default 40).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

MIN_NEAR_TOKENS = 6
# Different Career Cards in the catalog land at Hamming distance >= 8.
# A one-word edit of a long card lands around 1. The gate only picks candidates;
# Jaccard is the decision.
SIMHASH_GATE = 8
_CANDIDATE_CAP = 32
_IDENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_URL = re.compile(r"https?://\S+")
_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")

_cache: dict = {}


def _raw_budget():
    return os.environ.get("SIM_DAILY_POST_BUDGET", "30").strip().lower()


def enabled() -> bool:
    """False only when the operator turns the cap off. The default is on."""
    return _raw_budget() not in ("off", "unlimited", "none")


def post_budget() -> int:
    raw = _raw_budget()
    if raw in ("off", "unlimited", "none"):
        return 0
    try:
        value = int(raw)
    except ValueError:
        value = 30
    return max(0, min(value, 100000))


def career_share_cap() -> float:
    raw = os.environ.get("SIM_CAREER_SHARE_CAP", "0.4").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.4
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def dedupe_days() -> int:
    try:
        value = int(os.environ.get("SIM_DEDUPE_DAYS", "14"))
    except ValueError:
        value = 14
    return max(1, min(value, 3650))


def near_jaccard() -> float:
    raw = os.environ.get("SIM_NEAR_DUP_JACCARD", "0.90").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.90
    if value < 0.5:
        return 0.5
    if value > 1:
        return 1.0
    return value


def like_budget() -> int:
    try:
        value = int(os.environ.get("SIM_DAILY_LIKE_BUDGET", "120"))
    except ValueError:
        value = 120
    return max(0, min(value, 100000))


def follow_budget() -> int:
    try:
        value = int(os.environ.get("SIM_DAILY_FOLLOW_BUDGET", "40"))
    except ValueError:
        value = 40
    return max(0, min(value, 100000))


def utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


def _next_midnight(now: float) -> float:
    dt = datetime.fromtimestamp(now, timezone.utc)
    tomorrow = dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return tomorrow


def normalize(text) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _URL.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def _fnv1a64(token: str) -> int:
    digest = 14695981039346656037
    for byte in token.encode():
        digest ^= byte
        digest = (digest * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return digest


def simhash(tokens) -> int:
    bits = [0] * 64
    for token in tokens:
        hashed = _fnv1a64(token)
        for index in range(64):
            bits[index] += 1 if hashed & (1 << index) else -1
    out = 0
    for index, value in enumerate(bits):
        if value > 0:
            out |= 1 << index
    return out


def texts_are_near(left, right, threshold=None) -> bool:
    """Identical after normalization, or nearly the same token set.

    Texts shorter than MIN_NEAR_TOKENS match only when normalization makes
    them equal, so a one-word haiku is not treated as every other haiku.
    """
    a = normalize(left)
    b = normalize(right)
    if not a or not b:
        return False
    if a == b:
        return True
    tokens_a = a.split()
    tokens_b = b.split()
    if len(tokens_a) < MIN_NEAR_TOKENS or len(tokens_b) < MIN_NEAR_TOKENS:
        return False
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    union = len(set_a | set_b)
    if not union:
        return False
    score = len(set_a & set_b) / union
    return score >= (near_jaccard() if threshold is None else threshold)


def clear_cache(path=None):
    if path is None:
        _cache.clear()
    else:
        _cache.pop(path, None)


def _db_path(conn) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return ""
    name = row[2] if not hasattr(row, "keys") else row["file"]
    return name or ""


def ensure(conn):
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sim_quota (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            day TEXT NOT NULL,
            posts INTEGER NOT NULL DEFAULT 0,
            career_posts INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            follows INTEGER NOT NULL DEFAULT 0,
            next_post_at REAL NOT NULL DEFAULT 0,
            next_like_at REAL NOT NULL DEFAULT 0,
            next_follow_at REAL NOT NULL DEFAULT 0
        )"""
    )
    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='posts'"
    ).fetchone()
    if present:
        conn.execute("CREATE INDEX IF NOT EXISTS ix_posts_created ON posts(created)")


def _load(conn, now: float) -> dict:
    ensure(conn)
    day = utc_day(now)
    row = conn.execute("SELECT * FROM sim_quota WHERE id=1").fetchone()
    blank = {
        "day": day,
        "posts": 0,
        "career_posts": 0,
        "likes": 0,
        "follows": 0,
        "next_post_at": 0.0,
        "next_like_at": 0.0,
        "next_follow_at": 0.0,
    }
    if row is None or row["day"] != day:
        return blank
    return {
        "day": row["day"],
        "posts": int(row["posts"]),
        "career_posts": int(row["career_posts"]),
        "likes": int(row["likes"]),
        "follows": int(row["follows"]),
        "next_post_at": float(row["next_post_at"] or 0),
        "next_like_at": float(row["next_like_at"] or 0),
        "next_follow_at": float(row["next_follow_at"] or 0),
    }


def _write(conn, state: dict):
    conn.execute(
        """INSERT INTO sim_quota(
               id, day, posts, career_posts, likes, follows, next_post_at, next_like_at, next_follow_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             day=excluded.day,
             posts=excluded.posts,
             career_posts=excluded.career_posts,
             likes=excluded.likes,
             follows=excluded.follows,
             next_post_at=excluded.next_post_at,
             next_like_at=excluded.next_like_at,
             next_follow_at=excluded.next_follow_at""",
        (
            state["day"],
            state["posts"],
            state["career_posts"],
            state["likes"],
            state["follows"],
            state["next_post_at"],
            state["next_like_at"],
            state["next_follow_at"],
        ),
    )


def _career_room(state: dict, budget: int) -> bool:
    cap = career_share_cap()
    if state["career_posts"] >= int(budget * cap + 1e-9):
        return False
    posts = state["posts"]
    career_posts = state["career_posts"]
    return (career_posts + 1) / (posts + 1) <= cap + 1e-12


def slot_open(conn, now: float, career: bool = False) -> bool:
    if not enabled():
        return True
    budget = post_budget()
    if budget <= 0:
        return False
    state = _load(conn, now)
    if state["posts"] >= budget or now < state["next_post_at"]:
        return False
    if career and not _career_room(state, budget):
        return False
    return True


def reserve_post(conn, now: float, career: bool = False) -> bool:
    """Claim one simulated post. Caller holds the write transaction."""
    if not enabled():
        return True
    budget = post_budget()
    state = _load(conn, now)
    if budget <= 0 or state["posts"] >= budget or now < state["next_post_at"]:
        return False
    if career and not _career_room(state, budget):
        return False
    state["posts"] += 1
    if career:
        state["career_posts"] += 1
    state["next_post_at"] = now + (86400.0 / budget)
    _write(conn, state)
    return True


def _reserve_edge(conn, now: float, kind: str) -> bool:
    if not enabled():
        return True
    budget = like_budget() if kind == "likes" else follow_budget()
    next_key = "next_like_at" if kind == "likes" else "next_follow_at"
    state = _load(conn, now)
    if budget <= 0 or state[kind] >= budget or now < state[next_key]:
        return False
    state[kind] += 1
    state[next_key] = now + (86400.0 / budget)
    _write(conn, state)
    return True


def reserve_like(conn, now: float) -> bool:
    return _reserve_edge(conn, now, "likes")


def reserve_follow(conn, now: float) -> bool:
    return _reserve_edge(conn, now, "follows")


def suggest_sleep(conn, now: float) -> float:
    """Seconds until the next simulated post, like, or follow slot.

    Zero means a slot is already open. The loop should still wait at least
    TICK_SECONDS. The value is capped at 15 minutes so a new UTC day is not
    missed for long.
    """
    if not enabled():
        return 0.0
    state = _load(conn, now)
    waits = []
    posts = post_budget()
    if posts > 0 and state["posts"] < posts:
        waits.append(max(0.0, state["next_post_at"] - now))
    likes = like_budget()
    if likes > 0 and state["likes"] < likes:
        waits.append(max(0.0, state["next_like_at"] - now))
    follows = follow_budget()
    if follows > 0 and state["follows"] < follows:
        waits.append(max(0.0, state["next_follow_at"] - now))
    if not waits:
        waits.append(max(0.0, _next_midnight(now) - now))
    return min(900.0, min(waits))


def prefetch(conn, now: float):
    """Load the duplicate index before taking the write lock."""
    if not enabled():
        return
    _load_cache(conn, now)


def _load_cache(conn, now: float):
    path = _db_path(conn)
    cutoff = now - dedupe_days() * 86400.0
    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='posts'"
    ).fetchone()
    if not present:
        hit = {"stamp": (cutoff, 0, 0), "exact": set(), "sims": []}
        _cache[path] = hit
        return hit
    stamp_row = conn.execute(
        "SELECT COALESCE(MAX(id), 0), COUNT(*) FROM posts WHERE created >= ?",
        (cutoff,),
    ).fetchone()
    stamp = (float(cutoff), int(stamp_row[0]), int(stamp_row[1]))
    hit = _cache.get(path)
    if hit and hit["stamp"] == stamp:
        return hit
    exact = set()
    sims = []
    rows = conn.execute(
        "SELECT id, content FROM posts WHERE created >= ? AND content IS NOT NULL",
        (cutoff,),
    )
    for post_id, content in rows:
        norm = normalize(content)
        if not norm:
            continue
        digest = hashlib.sha256(norm.encode()).hexdigest()
        if digest in exact:
            continue
        exact.add(digest)
        tokens = norm.split()
        if len(tokens) >= MIN_NEAR_TOKENS:
            sims.append((int(post_id), simhash(tokens)))
    hit = {"stamp": stamp, "exact": exact, "sims": sims}
    _cache[path] = hit
    return hit


def is_near_duplicate(conn, content, now: float) -> bool:
    """True when content matches a post from the last SIM_DEDUPE_DAYS."""
    norm = normalize(content)
    if not norm:
        return False
    digest = hashlib.sha256(norm.encode()).hexdigest()
    hit = _load_cache(conn, now)
    if digest in hit["exact"]:
        return True
    tokens = norm.split()
    if len(tokens) < MIN_NEAR_TOKENS:
        return False
    hashed = simhash(tokens)
    candidates = []
    for post_id, other in hit["sims"]:
        distance = (hashed ^ other).bit_count()
        if distance <= SIMHASH_GATE:
            candidates.append((distance, post_id))
    if not candidates:
        return False
    candidates.sort()
    threshold = near_jaccard()
    for _distance, post_id in candidates[:_CANDIDATE_CAP]:
        row = conn.execute("SELECT content FROM posts WHERE id=?", (post_id,)).fetchone()
        if not row:
            continue
        other = row[0] if not hasattr(row, "keys") else row["content"]
        if texts_are_near(norm, other, threshold=threshold):
            return True
    return False


def snapshot(conn, now: float) -> dict:
    state = _load(conn, now)
    return {
        "enforced": enabled(),
        "post_budget": post_budget() if enabled() else None,
        "career_share_cap": career_share_cap(),
        "dedupe_days": dedupe_days(),
        "like_budget": like_budget() if enabled() else None,
        "follow_budget": follow_budget() if enabled() else None,
        "day": state["day"],
        "posts": state["posts"],
        "career_posts": state["career_posts"],
        "likes": state["likes"],
        "follows": state["follows"],
        "next_post_at": state["next_post_at"],
        "next_like_at": state["next_like_at"],
        "next_follow_at": state["next_follow_at"],
    }


def file_bytes(path: str) -> int:
    total = 0
    found = False
    for suffix in ("", "-wal", "-shm"):
        candidate = path + suffix
        if candidate and os.path.isfile(candidate):
            found = True
            total += os.path.getsize(candidate)
    return total if found else 0


def sqlite_overview(conn, path: str) -> dict:
    """File size plus a row count for every application table."""
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    tables = {}
    for name in names:
        if not _IDENT.fullmatch(name):
            continue
        tables[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    return {"path": path, "size_bytes": file_bytes(path), "tables": tables}
