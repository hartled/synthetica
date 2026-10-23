#!/usr/bin/env python3
"""Run one guarded prune in its own process and persist state as JSON.

The web process starts this and returns immediately:

  python scripts/prune_job.py --job /data/prune_jobs/<id>.json --db /data/botsocial.db

The job file already holds the request. This process writes pid, step, timings,
numbers, the backup path, and errors into that same file. It does not import
the web app, so a prune cannot hold the server's interpreter or its memory.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_NUMBER_KEYS = (
    "expected_delete",
    "expected_protected",
    "actual_delete",
    "actual_protected",
    "removed",
    "backup_bytes",
    "backup_posts",
)


def load_prune_posts():
    loaded = sys.modules.get("prune_posts")
    if loaded is not None and hasattr(loaded, "run_guarded_apply") and hasattr(loaded, "write_job"):
        return loaded
    path = ROOT / "scripts" / "prune_posts.py"
    spec = importlib.util.spec_from_file_location("prune_posts", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prune_posts"] = mod
    spec.loader.exec_module(mod)
    return mod


def merge_progress(mod, job_path, snap):
    state = mod.read_job(job_path)
    if state.get("status") not in (None, "running"):
        return
    state["status"] = "running"
    state["pid"] = os.getpid()
    if snap.get("step"):
        state["step"] = snap["step"]
    if snap.get("timings") is not None:
        state["timings"] = snap["timings"]
    if "backup_path" in snap:
        state["backup_path"] = snap.get("backup_path")
    numbers = dict(state.get("numbers") or {})
    for key in _NUMBER_KEYS:
        if snap.get(key) is not None:
            numbers[key] = snap[key]
    state["numbers"] = numbers
    mod.write_job(job_path, state)


def _numbers_from(result, current):
    numbers = dict(current or {})
    for key in _NUMBER_KEYS:
        if result.get(key) is not None:
            numbers[key] = result[key]
    return numbers


def audit_finish(db_path, actor, job_id, status, error):
    """Same row the web process used to write when a thread finished."""
    if not actor or not job_id:
        return
    detail = status if not error else f"{status}: {error}"
    try:
        conn = sqlite3.connect(db_path, timeout=30)
    except sqlite3.Error as exc:
        print("prune apply audit failed:", exc, flush=True)
        return
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "INSERT INTO security_audit_log(ts, actor_slug, action, target, ok, detail) VALUES(?,?,?,?,?,?)",
            (
                time.time(),
                actor,
                "prune_apply_finish",
                job_id,
                1 if status == "completed" else 0,
                str(detail)[:500],
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        print("prune apply audit failed:", exc, flush=True)
    finally:
        conn.close()


def run(job_path, db_path) -> int:
    mod = load_prune_posts()
    state = mod.read_job(job_path)
    job_id = state.get("id")
    actor = state.get("actor")
    state["pid"] = os.getpid()
    state["status"] = "running"
    mod.write_job(job_path, state)
    print(f"prune job {job_id}: start actor={actor}", flush=True)
    request = state.get("request") or {}

    def on_update(snap):
        try:
            merge_progress(mod, job_path, snap)
        except Exception as exc:
            print("prune job progress write failed:", exc, flush=True)

    try:
        result = mod.run_guarded_apply(
            db_path,
            expected_delete=request.get("expected_delete"),
            expected_protected=request.get("expected_protected"),
            tolerance_pct=request.get("tolerance_pct", 3),
            vacuum=bool(request.get("vacuum", True)),
            on_update=on_update,
        )
    except Exception as exc:
        traceback.print_exc()
        result = {"status": "failed", "step": state.get("step"), "error": str(exc)}

    final = mod.read_job(job_path)
    status = result.get("status") or "failed"
    error = result.get("error")
    final["status"] = status
    final["error"] = error
    final["result"] = result
    final["pid"] = os.getpid()
    if result.get("step"):
        final["step"] = result["step"]
    if result.get("timings") is not None:
        final["timings"] = result["timings"]
    if "backup_path" in result:
        final["backup_path"] = result.get("backup_path")
    final["finished_at"] = time.time()
    final["numbers"] = _numbers_from(result, final.get("numbers"))
    mod.write_job(job_path, final)
    print(f"prune job {job_id}: {status}", flush=True)
    audit_finish(db_path, actor, job_id, status, error)
    return 0 if status == "completed" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one persisted prune job and exit.")
    parser.add_argument("--job", required=True, help="JSON state file to read and update")
    parser.add_argument("--db", required=True, help="SQLite database to prune")
    args = parser.parse_args(argv)
    try:
        return run(args.job, args.db)
    except Exception as exc:
        traceback.print_exc()
        try:
            mod = load_prune_posts()
            state = mod.read_job(args.job)
            if state.get("status") == "running":
                state["status"] = "failed"
                state["error"] = str(exc)
                state["finished_at"] = time.time()
                state["pid"] = os.getpid()
                mod.write_job(args.job, state)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
