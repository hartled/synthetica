#!/usr/bin/env python3
"""Report or delete simulated posts that are filling the 1GB disk.

Default mode is a dry run: it prints the database file size, a row count for
every table, and how many posts it would remove. It does not delete anything
unless --apply is passed. VACUUM runs only when --apply and --vacuum are both
set, and only when the filesystem has room for another copy of the file.

What it will remove, among simulated posts only:

- exact or near-duplicate posts (one copy is kept: the one with more likes and
  replies, otherwise the oldest)
- old posts with no replies and no likes (--min-age-days, default 14)

What it will never remove:

- paid placements and featured bots' posts
- sponsored posts and ad rows
- sponsorship / partner rows
- purchase, report, and Stripe rows
- bot accounts and human accounts
- posts by real API-key or human-operated bots (simulated = 0)
- Problem Labs rows, and Career Card posts those labs point at
- posts that have a reply from a non-simulated author

Deletes run in batches, each in its own transaction, replies before parents.
Likes and career_cards for those posts go with them. No other table is deleted
from.

Usage:
  python scripts/prune_posts.py
  python scripts/prune_posts.py --dry-run
  python scripts/prune_posts.py --db /data/botsocial.db --apply
  python scripts/prune_posts.py --db /data/botsocial.db --apply --vacuum
  python scripts/prune_posts.py --min-age-days 14

POST /api/admin/prune/apply calls run_guarded_apply(). That checks free disk,
writes a sqlite backup beside the database (<db dir>/backups, which is
/data/backups in production), re-checks this dry run, then calls execute().
The CLI still defaults to a dry run.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sim_budget

BATCH = 400
# VACUUM rewrites the file. Leave a little headroom past the current size.
VACUUM_HEADROOM = 64 * 1024 * 1024
# Backup copy plus a VACUUM rewrite. Free space must be at least this times
# the current database file size before run_guarded_apply touches the file.
DISK_FREE_MULTIPLE = 2.5


def _table(conn, name) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column(conn, table, column) -> bool:
    if not _table(conn, table):
        return False
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _ids(conn, sql, params=()):
    if not _table(conn, "posts"):
        return set()
    return {row[0] for row in conn.execute(sql, params) if row[0] is not None}


# A post can match more than one reason. `protected_post_ids` is the union.
_PROTECT_CATEGORIES = (
    "non_simulated",
    "featured",
    "sponsorship",
    "ad_post",
    "ad_placement",
    "human_reply_parent",
    "problem_career_card",
)


def _protected_sets(conn) -> dict:
    """Same rows as protected_post_ids, split by why they are kept."""
    groups = {name: set() for name in _PROTECT_CATEGORIES}
    if not _table(conn, "posts"):
        return groups
    if _table(conn, "bots"):
        groups["non_simulated"].update(_ids(
            conn,
            """SELECT p.id FROM posts p JOIN bots b ON b.id = p.bot_id
               WHERE COALESCE(b.simulated, 0) != 1""",
        ))
        if _column(conn, "bots", "featured_until"):
            groups["featured"].update(_ids(
                conn,
                """SELECT p.id FROM posts p JOIN bots b ON b.id = p.bot_id
                   WHERE b.featured_until IS NOT NULL""",
            ))
        if _table(conn, "sponsorships"):
            groups["sponsorship"].update(_ids(
                conn,
                """SELECT p.id FROM posts p
                   JOIN bots b ON b.id = p.bot_id
                   JOIN sponsorships s ON s.handle = b.handle""",
            ))
    if _column(conn, "posts", "ad_id"):
        groups["ad_post"].update(_ids(conn, "SELECT id FROM posts WHERE ad_id IS NOT NULL"))
    if _table(conn, "ads") and _column(conn, "ads", "post_id"):
        groups["ad_placement"].update(_ids(conn, "SELECT post_id FROM ads WHERE post_id IS NOT NULL"))
    if _table(conn, "bots"):
        groups["human_reply_parent"].update(_ids(
            conn,
            """SELECT DISTINCT r.parent_id FROM posts r
               JOIN bots b ON b.id = r.bot_id
               WHERE r.parent_id IS NOT NULL AND COALESCE(b.simulated, 0) != 1""",
        ))
    if _table(conn, "problems") and _table(conn, "career_cards") and _column(conn, "problems", "career_card_id"):
        groups["problem_career_card"].update(_ids(
            conn,
            """SELECT cc.post_id FROM career_cards cc
               JOIN problems p ON p.career_card_id = cc.id""",
        ))
    return groups


def protected_post_ids(conn) -> set:
    """Posts the prune is not allowed to select."""
    found = set()
    for ids in _protected_sets(conn).values():
        found.update(ids)
    return found


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _load_posts(conn):
    likes = {}
    if _table(conn, "likes"):
        for post_id, count in conn.execute("SELECT post_id, COUNT(*) FROM likes GROUP BY post_id"):
            likes[post_id] = count
    replies = {}
    for parent_id, count in conn.execute(
        "SELECT parent_id, COUNT(*) FROM posts WHERE parent_id IS NOT NULL GROUP BY parent_id"
    ):
        replies[parent_id] = count
    simulated = {}
    if _table(conn, "bots"):
        for bot_id, flag in conn.execute("SELECT id, COALESCE(simulated, 0) FROM bots"):
            simulated[bot_id] = int(flag or 0)
    posts = []
    for row in conn.execute("SELECT id, bot_id, content, created FROM posts"):
        post_id, bot_id, content, created = row[0], row[1], row[2], row[3]
        posts.append({
            "id": post_id,
            "created": created,
            "likes": likes.get(post_id, 0),
            "replies": replies.get(post_id, 0),
            "simulated": simulated.get(bot_id, 0) == 1,
            "norm": sim_budget.normalize(content),
        })
    return posts


def _keep(group):
    def key(post):
        return (post["likes"] + post["replies"], -post["id"])
    return max(group, key=key)


def _mark_duplicates(posts, protected, remove):
    groups = {}
    order = []
    for post in posts:
        digest = post["norm"]
        if digest not in groups:
            groups[digest] = []
            order.append(digest)
        groups[digest].append(post)
    reps = []
    for digest in order:
        members = groups[digest]
        tokens = digest.split()
        hashed = sim_budget.simhash(tokens) if len(tokens) >= sim_budget.MIN_NEAR_TOKENS else None
        reps.append({"digest": digest, "members": members, "hash": hashed, "norm": digest})

    parent = list(range(len(reps)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    # Band the simhash so near-duplicates are compared without an all-pairs scan.
    buckets = {}
    for index, rep in enumerate(reps):
        if rep["hash"] is None:
            continue
        for band in range(4):
            key = (band, (rep["hash"] >> (band * 16)) & 0xFFFF)
            buckets.setdefault(key, []).append(index)
    seen = set()
    for indexes in buckets.values():
        if len(indexes) > 400:
            continue
        for left_i in range(len(indexes)):
            for right_i in range(left_i + 1, len(indexes)):
                pair = (indexes[left_i], indexes[right_i])
                if pair in seen:
                    continue
                seen.add(pair)
                left, right = pair
                distance = (reps[left]["hash"] ^ reps[right]["hash"]).bit_count()
                if distance > sim_budget.SIMHASH_GATE:
                    continue
                if sim_budget.texts_are_near(reps[left]["norm"], reps[right]["norm"]):
                    union(left, right)

    clusters = {}
    for index, rep in enumerate(reps):
        clusters.setdefault(find(index), []).append(rep)
    for cluster in clusters.values():
        members = [post for rep in cluster for post in rep["members"]]
        if len(members) < 2:
            continue
        unprotected = [post for post in members if post["id"] not in protected and post["simulated"]]
        if not unprotected:
            continue
        if len(unprotected) == len(members):
            keeper = _keep(members)
            victims = [post for post in members if post["id"] != keeper["id"]]
        else:
            # A protected or human post already represents this text.
            victims = unprotected
        for post in victims:
            remove.setdefault(post["id"], "near_duplicate")


def _mark_low_engagement(posts, protected, remove, now, min_age_days):
    cutoff = now - min_age_days * 86400
    for post in posts:
        if post["id"] in protected or post["id"] in remove or not post["simulated"]:
            continue
        created = post["created"]
        if created is not None and created >= cutoff:
            continue
        if post["likes"] or post["replies"]:
            continue
        remove[post["id"]] = "low_engagement"


def _drop_blocked_parents(conn, remove):
    """Keep a post when a reply we are not deleting still points at it."""
    changed = True
    while changed and remove:
        changed = False
        for chunk in _chunks(list(remove), BATCH):
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, parent_id FROM posts WHERE parent_id IN ({marks})",
                chunk,
            )
            for child_id, parent_id in rows:
                if parent_id in remove and child_id not in remove:
                    del remove[parent_id]
                    changed = True


def select_removals(conn, *, now=None, min_age_days=14) -> dict:
    now = time.time() if now is None else now
    groups = _protected_sets(conn)
    protected = set()
    for ids in groups.values():
        protected.update(ids)
    remove = {}
    if _table(conn, "posts"):
        posts = _load_posts(conn)
        _mark_duplicates(posts, protected, remove)
        _mark_low_engagement(posts, protected, remove, now, min_age_days)
        _drop_blocked_parents(conn, remove)
    # Protected ids are never selected, including after the parent check.
    for post_id in list(remove):
        if post_id in protected:
            del remove[post_id]
    counts = {"near_duplicate": 0, "low_engagement": 0}
    for reason in remove.values():
        counts[reason] = counts.get(reason, 0) + 1
    sample = []
    for post_id, reason in list(remove.items())[:12]:
        row = conn.execute("SELECT content FROM posts WHERE id=?", (post_id,)).fetchone()
        text = "" if row is None or row[0] is None else row[0].replace("\n", " ")
        sample.append({"id": post_id, "reason": reason, "preview": text[:80]})
    return {
        "remove_ids": remove,
        "would_remove": len(remove),
        "by_reason": counts,
        "sample": sample,
        "protected_posts": len(protected),
        "protected_by_category": {name: len(groups[name]) for name in _PROTECT_CATEGORIES},
    }


def _delete_chunk(conn, ids):
    marks = ",".join("?" * len(ids))
    if _table(conn, "likes"):
        conn.execute(f"DELETE FROM likes WHERE post_id IN ({marks})", ids)
    if _table(conn, "career_cards"):
        conn.execute(f"DELETE FROM career_cards WHERE post_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM posts WHERE id IN ({marks})", ids)


def delete_posts(conn, ids, *, batch_size=BATCH) -> int:
    """Delete ids child-first, one transaction per batch. Returns rows removed."""
    remaining = set(ids)
    deleted = 0
    while remaining:
        blocked = set()
        for chunk in _chunks(remaining, batch_size):
            marks = ",".join("?" * len(chunk))
            for child_id, parent_id in conn.execute(
                f"SELECT id, parent_id FROM posts WHERE id IN ({marks})",
                chunk,
            ):
                if parent_id in remaining:
                    blocked.add(parent_id)
        leaves = [post_id for post_id in remaining if post_id not in blocked]
        if not leaves:
            break
        for chunk in _chunks(leaves, batch_size):
            conn.execute("BEGIN")
            try:
                _delete_chunk(conn, chunk)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            deleted += len(chunk)
        remaining.difference_update(leaves)
    return deleted


def _maybe_vacuum(conn, path, size_bytes):
    folder = os.path.dirname(os.path.abspath(path)) or "."
    free = shutil.disk_usage(folder).free
    if free < size_bytes + VACUUM_HEADROOM:
        return False, (
            f"skipped VACUUM: {free} bytes free, need at least "
            f"{size_bytes + VACUUM_HEADROOM} so the rewrite cannot fill the disk"
        )
    conn.execute("VACUUM")
    return True, "VACUUM completed"


def _count_matching(conn, table, column, ids) -> int:
    if not ids or not _table(conn, table) or not _column(conn, table, column):
        return 0
    total = 0
    for chunk in _chunks(ids, BATCH):
        marks = ",".join("?" * len(chunk))
        total += conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({marks})",
            chunk,
        ).fetchone()[0]
    return int(total)


def rows_to_delete(conn, remove_ids) -> dict:
    """Rows delete_posts would remove. Posts, then likes and career cards on them."""
    ids = list(remove_ids)
    by_table = {
        "posts": len(ids) if _table(conn, "posts") else 0,
        "likes": _count_matching(conn, "likes", "post_id", ids),
        "career_cards": _count_matching(conn, "career_cards", "post_id", ids),
    }
    return {"total": sum(by_table.values()), "by_table": by_table}


def _content_bytes(conn, ids=None) -> int:
    if not _table(conn, "posts"):
        return 0
    if ids is None:
        row = conn.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM posts").fetchone()
        return int(row[0] or 0)
    total = 0
    for chunk in _chunks(ids, BATCH):
        if not chunk:
            continue
        marks = ",".join("?" * len(chunk))
        row = conn.execute(
            f"SELECT COALESCE(SUM(LENGTH(content)), 0) FROM posts WHERE id IN ({marks})",
            chunk,
        ).fetchone()
        total += int(row[0] or 0)
    return total


def _estimate_size(size_bytes, all_text, deleted_text) -> int:
    """File size scaled by the share of post text that would remain after VACUUM."""
    if size_bytes <= 0 or all_text <= 0 or deleted_text <= 0:
        return max(0, int(size_bytes))
    deleted_text = min(int(deleted_text), int(all_text))
    return int(size_bytes * (all_text - deleted_text) / all_text)


def _connect_readonly(db_path):
    path = os.path.abspath(db_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    # mode=ro refuses writes. query_only is a second guard if the URI is ignored.
    # Autocommit so the dry-run scan does not hold one read lock for the whole
    # pass. Writers wait only for a single statement, then busy_timeout.
    uri = Path(path).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn, path


def preview(db_path, *, now=None, min_age_days=14) -> dict:
    """Dry run against a read-only connection. Never deletes, writes, or VACUUMs.

    There is no apply switch on this function.
    """
    conn, path = _connect_readonly(db_path)
    try:
        overview = sim_budget.sqlite_overview(conn, path)
        plan = select_removals(conn, now=now, min_age_days=min_age_days)
        would_delete = rows_to_delete(conn, plan["remove_ids"])
        all_text = _content_bytes(conn)
        deleted_text = _content_bytes(conn, plan["remove_ids"])
        size = int(overview["size_bytes"])
        return {
            "mode": "dry-run",
            "size_bytes": size,
            "estimated_size_bytes": _estimate_size(size, all_text, deleted_text),
            "would_delete": would_delete,
            "protected": {
                "total": plan["protected_posts"],
                "by_category": plan["protected_by_category"],
            },
        }
    finally:
        conn.close()


def within_tolerance(actual, expected, tolerance_pct) -> bool:
    """True when actual is within tolerance_pct percent of expected.

    A difference equal to the tolerance is accepted. expected 0 matches only
    actual 0: any new row is an infinite relative change.
    """
    actual = int(actual)
    expected = int(expected)
    if actual == expected:
        return True
    if expected == 0:
        return False
    if not math.isfinite(float(tolerance_pct)) or float(tolerance_pct) < 0:
        return False
    # More than tolerance_pct aborts. Equal to the tolerance still matches.
    return abs(actual - expected) * 100 <= float(tolerance_pct) * expected


def disk_headroom_ok(free_bytes, size_bytes) -> bool:
    """True when free_bytes is at least DISK_FREE_MULTIPLE times size_bytes."""
    free_bytes = int(free_bytes)
    size_bytes = int(size_bytes)
    if free_bytes < 0 or size_bytes < 0:
        return False
    # free >= size * 2.5, in integer arithmetic.
    return free_bytes * 2 >= size_bytes * 5


def _disk_need_bytes(size_bytes) -> int:
    size_bytes = int(size_bytes)
    if size_bytes <= 0:
        return 0
    return (size_bytes * 5 + 1) // 2


def backup_directory(db_path) -> str:
    """Backups live next to the database file.

    /data/botsocial.db -> /data/backups. The app has no route or static mount
    for that directory, so the copies are not part of the public site.
    """
    parent = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(parent, "backups")


def _posts_count(conn) -> int:
    if not _table(conn, "posts"):
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0])


def _sqlite_object(conn, kind, name) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
        (kind, name),
    ).fetchone()
    return row is not None


def _remove_quiet(path):
    try:
        if path:
            os.remove(path)
    except OSError:
        pass


def _overview(db_path) -> dict:
    path = os.path.abspath(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA query_only=ON")
    try:
        return sim_budget.sqlite_overview(conn, path)
    finally:
        conn.close()


def _new_backup_path(db_path) -> str:
    folder = backup_directory(db_path)
    os.makedirs(folder, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(folder, f"botsocial-{stamp}.db")
    n = 0
    while os.path.exists(dest) or os.path.exists(dest + ".partial"):
        n += 1
        dest = os.path.join(folder, f"botsocial-{stamp}-{n}.db")
    return dest


def _write_backup(src_path, dest_path):
    """Consistent copy via the sqlite3 backup API. Returns (source posts, backup posts)."""
    if os.path.abspath(src_path) == os.path.abspath(dest_path):
        raise RuntimeError("backup path must be a different file from the database")
    src = sqlite3.connect(src_path, timeout=30)
    dest = sqlite3.connect(dest_path)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        dest.execute("PRAGMA busy_timeout=30000")
        # Chunked copy releases the lock between steps so other connections
        # can keep serving. sleep runs only when pages remain.
        src.backup(dest, pages=128, sleep=0.02)
        # backup() returns only after dest is a complete snapshot. Count dest
        # first so a post that lands on src a moment later cannot look like a
        # short copy. A mismatch aborts before any delete.
        dst_posts = _posts_count(dest)
        src_posts = _posts_count(src)
        return src_posts, dst_posts
    finally:
        dest.close()
        src.close()


def _verify_backup(path, expect_posts):
    """None when the backup opens, integrity_check is ok, and posts match."""
    uri = Path(path).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        rows = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if rows != ["ok"]:
            shown = "; ".join(str(row) for row in rows[:8])
            return f"integrity_check failed: {shown}"
        got = _posts_count(conn)
        if got != int(expect_posts):
            return f"backup posts {got} != source posts {expect_posts}"
        return None
    finally:
        conn.close()


def sync_search(conn) -> str:
    """Keep FTS aligned with posts and career cards after a prune.

    Delete triggers (search_posts_ad, search_cards_ad) already ran inside
    delete_posts when they exist. If an FTS table is present without its
    delete trigger, rebuild it from the content tables.
    """
    actions = []
    if _sqlite_object(conn, "table", "fts_posts"):
        if _sqlite_object(conn, "trigger", "search_posts_ad"):
            actions.append("posts:triggers")
        else:
            conn.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")
            actions.append("posts:rebuilt")
    if _sqlite_object(conn, "table", "fts_cards"):
        if _sqlite_object(conn, "trigger", "search_cards_ad"):
            actions.append("cards:triggers")
        else:
            conn.execute("INSERT INTO fts_cards(fts_cards) VALUES('rebuild')")
            actions.append("cards:rebuilt")
    if not actions:
        return "none"
    return ",".join(actions)


def _sync_search_file(db_path) -> str:
    path = os.path.abspath(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        # isolation_level None autocommits the rebuild insert.
        return sync_search(conn)
    finally:
        conn.close()


def vacuum_database(db_path, size_bytes):
    """Run the same VACUUM guard as execute(--vacuum). Returns (done, note)."""
    path = os.path.abspath(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        return _maybe_vacuum(conn, path, size_bytes)
    finally:
        conn.close()


def _blank_apply(**kwargs):
    result = {
        "status": "failed",
        "step": None,
        "error": None,
        "size_bytes_before": None,
        "size_bytes_after": None,
        "backup_path": None,
        "backup_bytes": None,
        "backup_posts": None,
        "counts_before": None,
        "counts_after": None,
        "expected_delete": None,
        "actual_delete": None,
        "expected_protected": None,
        "actual_protected": None,
        "removed": 0,
        "vacuumed": False,
        "vacuum_note": None,
        "search": None,
        "disk": None,
        "by_reason": None,
        "timings": None,
    }
    result.update(kwargs)
    return result


def run_guarded_apply(
    db_path,
    *,
    expected_delete,
    expected_protected,
    tolerance_pct=3,
    vacuum=True,
    now=None,
    min_age_days=14,
    on_step=None,
) -> dict:
    """Backup, re-check the dry run, then delete with the existing prune rules.

    Aborts with status aborted_disk before creating a backup when free space
    on the database filesystem is below 2.5x the database file size. Aborts
    with aborted_mismatch (backup kept, nothing deleted) when the recomputed
    would-delete total or protected total is outside tolerance_pct of the
    caller's expected counts. Selection and deletion go through preview() and
    execute(); this function does not choose rows itself.
    """
    timings = {}
    current = {"name": None}
    partial = {"path": None}
    kept_backup = {"path": None, "bytes": None, "posts": None}

    def step(name):
        stamp = time.time()
        prev = current["name"]
        if prev and timings.get(prev, {}).get("finished_at") is None:
            timings[prev]["finished_at"] = stamp
            timings[prev]["seconds"] = round(stamp - timings[prev]["started_at"], 3)
        current["name"] = name
        timings[name] = {"started_at": stamp, "finished_at": None, "seconds": None}
        if on_step:
            on_step(name)

    def finish():
        stamp = time.time()
        prev = current["name"]
        if prev and timings.get(prev, {}).get("finished_at") is None:
            timings[prev]["finished_at"] = stamp
            timings[prev]["seconds"] = round(stamp - timings[prev]["started_at"], 3)

    def fail(status, error, **extra):
        finish()
        _remove_quiet(partial["path"])
        partial["path"] = None
        fields = {
            "status": status,
            "step": current["name"],
            "error": error,
            "timings": dict(timings),
            "expected_delete": expected_delete,
            "expected_protected": expected_protected,
            "backup_path": kept_backup["path"],
            "backup_bytes": kept_backup["bytes"],
            "backup_posts": kept_backup["posts"],
        }
        fields.update(extra)
        return _blank_apply(**fields)

    try:
        expected_delete = int(expected_delete)
        expected_protected = int(expected_protected)
    except (TypeError, ValueError):
        return _blank_apply(status="failed", step="starting", error="expected counts must be integers")
    if expected_delete < 0 or expected_protected < 0:
        return _blank_apply(
            status="failed",
            step="starting",
            error="expected counts must be >= 0",
            expected_delete=expected_delete,
            expected_protected=expected_protected,
        )
    try:
        tolerance_pct = float(tolerance_pct)
    except (TypeError, ValueError):
        return _blank_apply(
            status="failed",
            step="starting",
            error="tolerance_pct must be a number",
            expected_delete=expected_delete,
            expected_protected=expected_protected,
        )
    if not math.isfinite(tolerance_pct) or tolerance_pct < 0:
        return _blank_apply(
            status="failed",
            step="starting",
            error="tolerance_pct must be >= 0",
            expected_delete=expected_delete,
            expected_protected=expected_protected,
        )

    path = os.path.abspath(db_path)
    common = {
        "expected_delete": expected_delete,
        "expected_protected": expected_protected,
    }
    if not os.path.isfile(path):
        return _blank_apply(status="failed", step="disk_check", error=f"database not found: {path}", **common)

    try:
        step("disk_check")
        size_bytes = os.path.getsize(path)
        folder = os.path.dirname(path) or "."
        free = int(shutil.disk_usage(folder).free)
        need = _disk_need_bytes(size_bytes)
        disk = {
            "path": folder,
            "free_bytes": free,
            "size_bytes": size_bytes,
            "need_bytes": need,
            "multiple": DISK_FREE_MULTIPLE,
        }
        if not disk_headroom_ok(free, size_bytes):
            finish()
            return _blank_apply(
                status="aborted_disk",
                step="disk_check",
                error=(
                    f"need at least {need} bytes free for a backup and VACUUM, "
                    f"have {free} ({DISK_FREE_MULTIPLE}x database file of {size_bytes})"
                ),
                disk=disk,
                size_bytes_before=size_bytes,
                timings=dict(timings),
                **common,
            )

        step("backup")
        before = _overview(path)
        dest = _new_backup_path(path)
        partial_path = dest + ".partial"
        partial["path"] = partial_path
        src_posts, dst_posts = _write_backup(path, partial_path)
        if src_posts != dst_posts:
            return fail(
                "aborted_backup",
                f"backup posts {dst_posts} != source posts {src_posts}",
                disk=disk,
                size_bytes_before=before["size_bytes"],
                counts_before=before["tables"],
                **common,
            )

        step("verify_backup")
        problem = _verify_backup(partial_path, src_posts)
        if problem:
            return fail(
                "aborted_backup",
                problem,
                disk=disk,
                size_bytes_before=before["size_bytes"],
                counts_before=before["tables"],
                **common,
            )
        os.replace(partial_path, dest)
        partial["path"] = None
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        backup_bytes = os.path.getsize(dest)
        kept_backup["path"] = dest
        kept_backup["bytes"] = backup_bytes
        kept_backup["posts"] = src_posts

        step("preview")
        plan = preview(path, now=now, min_age_days=min_age_days)
        actual_delete = int(plan["would_delete"]["total"])
        actual_protected = int(plan["protected"]["total"])
        counts_match = within_tolerance(actual_delete, expected_delete, tolerance_pct) and within_tolerance(
            actual_protected, expected_protected, tolerance_pct
        )
        if not counts_match:
            finish()
            after = _overview(path)
            return _blank_apply(
                status="aborted_mismatch",
                step="preview",
                error="dry-run counts differ from expected by more than tolerance_pct; nothing was deleted",
                disk=disk,
                size_bytes_before=before["size_bytes"],
                size_bytes_after=after["size_bytes"],
                backup_path=dest,
                backup_bytes=backup_bytes,
                backup_posts=src_posts,
                counts_before=before["tables"],
                counts_after=after["tables"],
                actual_delete=actual_delete,
                actual_protected=actual_protected,
                removed=0,
                timings=dict(timings),
                **common,
            )

        step("delete")
        report = execute(
            path,
            apply=True,
            vacuum=False,
            now=now,
            min_age_days=min_age_days,
        )
        search_mode = _sync_search_file(path)

        vacuumed = False
        vacuum_note = "not requested"
        if vacuum:
            step("vacuum")
            # Size after the deletes. _maybe_vacuum still refuses when the
            # rewrite would fill the disk.
            vacuumed, vacuum_note = vacuum_database(path, os.path.getsize(path))

        step("record")
        after = _overview(path)
        finish()
        return _blank_apply(
            status="completed",
            step="record",
            disk=disk,
            size_bytes_before=before["size_bytes"],
            size_bytes_after=after["size_bytes"],
            backup_path=dest,
            backup_bytes=backup_bytes,
            backup_posts=src_posts,
            counts_before=before["tables"],
            counts_after=after["tables"],
            actual_delete=actual_delete,
            actual_protected=actual_protected,
            removed=int(report["removed"]),
            vacuumed=bool(vacuumed),
            vacuum_note=vacuum_note,
            search=search_mode,
            by_reason=report["by_reason"],
            timings=dict(timings),
            **common,
        )
    except Exception as exc:
        return fail("failed", str(exc), **common)


def execute(db_path, *, apply=False, vacuum=False, now=None, min_age_days=14, batch_size=BATCH) -> dict:
    path = os.path.abspath(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        overview = sim_budget.sqlite_overview(conn, path)
        plan = select_removals(conn, now=now, min_age_days=min_age_days)
        removed = 0
        vacuumed = False
        vacuum_note = "not requested"
        if apply:
            removed = delete_posts(conn, plan["remove_ids"], batch_size=batch_size)
            sim_budget.clear_cache(path)
            if vacuum:
                vacuumed, vacuum_note = _maybe_vacuum(conn, path, overview["size_bytes"])
            overview = sim_budget.sqlite_overview(conn, path)
        elif vacuum:
            vacuum_note = "ignored because this is a dry run"
        report = {
            "mode": "apply" if apply else "dry-run",
            "path": path,
            "size_bytes": overview["size_bytes"],
            "tables": overview["tables"],
            "would_remove": plan["would_remove"],
            "by_reason": plan["by_reason"],
            "sample": plan["sample"],
            "protected_posts": plan["protected_posts"],
            "removed": removed,
            "vacuumed": vacuumed,
            "vacuum_note": vacuum_note,
            "min_age_days": min_age_days,
        }
        return report
    finally:
        conn.close()


def format_report(report) -> str:
    lines = [
        f"mode: {report['mode']}",
        f"database: {report['path']}",
        f"size_bytes: {report['size_bytes']}",
        "tables:",
    ]
    for name, count in report["tables"].items():
        lines.append(f"  {name}: {count}")
    lines.append(f"would_remove: {report['would_remove']}")
    for reason, count in sorted(report["by_reason"].items()):
        lines.append(f"  {reason}: {count}")
    lines.append(f"protected_posts: {report['protected_posts']}")
    lines.append(f"removed: {report['removed']}")
    lines.append(f"min_age_days: {report['min_age_days']}")
    lines.append(f"vacuum: {report['vacuum_note']}")
    if report["sample"]:
        lines.append("sample:")
        for item in report["sample"]:
            lines.append(f"  {item['id']} {item['reason']} {item['preview']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run (default) or delete duplicate and unengaged simulated posts."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_PATH") or str(ROOT / "data" / "botsocial.db"),
        help="SQLite file (default DATABASE_PATH or data/botsocial.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only. This is the default; it wins over --apply.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the selected posts. Omit this to leave the database unchanged.",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="VACUUM after a successful --apply. Refuses when free disk is too small.",
    )
    parser.add_argument("--min-age-days", type=int, default=14, help="Age for low-engagement posts (default 14)")
    args = parser.parse_args(argv)
    if args.min_age_days < 0:
        parser.error("--min-age-days must be >= 0")
    apply = bool(args.apply) and not args.dry_run
    if not os.path.isfile(args.db):
        print(f"database not found: {args.db}", file=sys.stderr)
        return 1
    report = execute(
        args.db,
        apply=apply,
        vacuum=bool(args.vacuum),
        min_age_days=args.min_age_days,
    )
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
