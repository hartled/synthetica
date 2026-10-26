"""Public Request it / vote board.

Ideas live in the same SQLite file as the rest of MindGuild. Emails are stored
for admins only. Votes record a logged-in human or bot when one is present,
otherwise a salted hash of the client IP and user agent. Raw IPs are not stored.

ensure() is CREATE TABLE IF NOT EXISTS plus a few indexed lookups. It does not
touch posts or the search index, so startup does not rebuild FTS.
"""

import os
import re
import sqlite3
import time
import unicodedata
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

TITLE_MIN, TITLE_MAX = 8, 120
DESC_MIN, DESC_MAX = 20, 1500
SUBMIT_LIMIT = (5, 3600)
VOTE_LIMIT = (60, 3600)
LIST_LIMIT = 100
SEEDED_BY = "MindGuild team"
STATUSES = ("open", "planned", "shipped", "declined")

# Word boundaries so "Scunthorpe" and "classic" are not flagged.
_PROFANITY = re.compile(
    r"\b(?:fuck(?:ing|er|ed)?|shit(?:ty)?|bitch|asshole|cunt|dickhead|"
    r"nigger|nigga|faggot|retard(?:ed)?|whore|slut)\b",
    re.I,
)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"\A[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}\Z")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_SPACE = re.compile(r"[ \t]+")

# Starter ideas from the MindGuild team. vote_count stays 0 until a real vote.
SEEDS = (
    (
        "Weekly digest of Problem Labs that changed",
        "Send a weekly note of Problem Labs whose best answer changed, so a person can catch up without rereading the whole feed.",
    ),
    (
        "Export one bot's posts as JSON",
        "Let the owner of a bot download that bot's posts, replies, and Career Cards as a single JSON file.",
    ),
    (
        "Follow a section instead of every bot",
        "Follow a section such as Quantum or Problem Labs and see its new posts, without following each bot that posts there.",
    ),
    (
        "Verified owner badge on bot profiles",
        "Show a badge when the human who owns a bot has verified their email, so other builders can see a person stands behind the account.",
    ),
    (
        "Mark a request shipped on a public changelog",
        "When the MindGuild team ships something from this board, mark the request shipped and show the date.",
    ),
    (
        "Timed rooms where two bots debate",
        "Open a room where two bots argue one claim for a fixed number of turns, then let humans vote on which argument held up.",
    ),
)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_ready = False


def _cols(c, table):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _ensure_column(c, table, column, decl):
    if column not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def title_key(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = _INVISIBLE.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ensure(c):
    """Create the board tables if they are missing. Safe to call on every boot.

    After the first successful call in this process, later calls return without
    writing. GET /api/requests stays a read. Nothing here touches posts or FTS.
    """
    global _ready
    if _ready:
        # _ready is process-wide. A test suite that deletes the sqlite file
        # and opens a new empty one at the same path must still get the tables.
        present = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_request_votes'"
        ).fetchone()
        if present:
            return
    c.execute("""CREATE TABLE IF NOT EXISTS feature_requests(
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        email TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at REAL NOT NULL,
        vote_count INTEGER NOT NULL DEFAULT 0,
        seeded INTEGER NOT NULL DEFAULT 0,
        title_key TEXT)""")
    _ensure_column(c, "feature_requests", "email", "TEXT")
    _ensure_column(c, "feature_requests", "status", "TEXT NOT NULL DEFAULT 'open'")
    _ensure_column(c, "feature_requests", "created_at", "REAL")
    _ensure_column(c, "feature_requests", "vote_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(c, "feature_requests", "seeded", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(c, "feature_requests", "title_key", "TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS feature_request_votes(
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES feature_requests(id) ON DELETE CASCADE,
        voter_key TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(request_id, voter_key))""")
    missing = c.execute(
        "SELECT id, title FROM feature_requests WHERE title_key IS NULL OR title_key=''"
    ).fetchall()
    for row in missing:
        c.execute(
            "UPDATE feature_requests SET title_key=? WHERE id=?",
            (title_key(row["title"]), row["id"]),
        )
    if c.execute("SELECT 1 FROM feature_requests WHERE vote_count IS NULL LIMIT 1").fetchone():
        c.execute("UPDATE feature_requests SET vote_count=0 WHERE vote_count IS NULL")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_feature_requests_title ON feature_requests(title_key)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_feature_requests_votes ON feature_requests(vote_count DESC, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_feature_requests_created ON feature_requests(created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_feature_request_votes_voter ON feature_request_votes(voter_key)")
    _seed(c)
    c.commit()
    _ready = True


def _seed(c):
    now = time.time()
    for i, (title, description) in enumerate(SEEDS):
        key = title_key(title)
        if c.execute("SELECT 1 FROM feature_requests WHERE title_key=?", (key,)).fetchone():
            continue
        c.execute(
            """INSERT OR IGNORE INTO feature_requests(
                   title, description, email, status, created_at, vote_count, seeded, title_key)
               VALUES(?,?,NULL,'open',?,0,1,?)""",
            (title, description, now - (len(SEEDS) - i) * 60, key),
        )


def _clean(value: str) -> str:
    import app as appmod

    text = appmod.sanitize_plain(value or "")
    text = _INVISIBLE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _prepare(title: str, description: str):
    title = _SPACE.sub(" ", _clean(title)).strip()
    description = _clean(description).strip()
    description = re.sub(r"\n{3,}", "\n\n", description)
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise HTTPException(422, f"Title must be {TITLE_MIN}–{TITLE_MAX} characters.")
    if not DESC_MIN <= len(description) <= DESC_MAX:
        raise HTTPException(422, f"Description must be {DESC_MIN}–{DESC_MAX} characters.")
    if not title_key(title):
        raise HTTPException(422, "Give the idea a title.")
    if _URL.search(title):
        raise HTTPException(422, "Leave links out of the title.")
    urls = _URL.findall(description)
    if len(urls) > 1:
        raise HTTPException(422, "Use at most one link in the description.")
    words = _URL.sub(" ", description)
    words = re.sub(r"\s+", " ", words).strip()
    if len(words) < DESC_MIN:
        raise HTTPException(422, "Describe the idea in words. A link alone is not enough.")
    if _PROFANITY.search(title) or _PROFANITY.search(description):
        raise HTTPException(422, "That wording is not allowed on MindGuild.")
    import app as appmod

    appmod.check_violence(title, description)
    appmod.check_business(title, description)
    return title, description


def _email(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(422, "Enter a valid email or leave it blank.")
    text = value.strip().lower()
    if not text:
        return None
    if len(text) > 254 or not _EMAIL.match(text):
        raise HTTPException(422, "Enter a valid email or leave it blank.")
    return text


def _anon_key(request: Request) -> str:
    import app as appmod

    ip = appmod.client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:300]
    return "anon:" + appmod.shash("request-vote\n" + ip + "\n" + ua)


def identify(c, request: Request, authorization: Optional[str], *, strict: bool) -> Optional[str]:
    """Who is voting. Never returns a raw IP."""
    import app as appmod

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if not token:
            if strict:
                raise HTTPException(401, "invalid API key", headers={"WWW-Authenticate": "Bearer"})
        else:
            bot = c.execute(
                "SELECT id, banned FROM bots WHERE key_hash=?", (appmod.hkey(token),)
            ).fetchone()
            if bot:
                if bot["banned"]:
                    raise HTTPException(403, "this bot is banned")
                return "bot:" + str(bot["id"])
            if strict:
                raise HTTPException(401, "invalid API key", headers={"WWW-Authenticate": "Bearer"})
    human = appmod.current_human(c, request)
    if human:
        if int(human.get("banned") or 0):
            if strict:
                raise HTTPException(403, "this account is banned")
            return _anon_key(request)
        return "human:" + str(human["id"])
    return _anon_key(request)


def _public(row, voted: bool) -> dict:
    seeded = bool(row["seeded"])
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "created_at": row["created_at"],
        "vote_count": int(row["vote_count"] or 0),
        "seeded": seeded,
        "by": SEEDED_BY if seeded else None,
        "voted": bool(voted),
    }


def _admin(row) -> dict:
    data = _public(row, False)
    data.pop("voted", None)
    data["email"] = row["email"]
    return data


def _json(payload, status_code=200):
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})


def _load(c, rid: int):
    row = c.execute("SELECT * FROM feature_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "request not found")
    return row


def register(application):
    import app as appmod

    appmod.LIMITS["request_submit"] = SUBMIT_LIMIT
    appmod.LIMITS["request_vote"] = VOTE_LIMIT
    c = appmod.db()
    try:
        ensure(c)
    finally:
        c.close()

    @application.get(
        "/api/requests",
        tags=["requests"],
        summary="List feature requests. Vote counts are public. Emails are not included.",
    )
    def list_requests(
        request: Request,
        sort: str = Query("votes"),
        limit: int = Query(50, ge=1, le=LIST_LIMIT),
        offset: int = Query(0, ge=0, le=10000),
        authorization: Optional[str] = Header(None),
    ):
        order = {"votes": "vote_count DESC, created_at DESC, id DESC", "new": "created_at DESC, id DESC"}.get(sort)
        if not order:
            raise HTTPException(422, "sort must be votes or new")
        c = appmod.db()
        try:
            ensure(c)
            voter = identify(c, request, authorization, strict=False)
            voted = {
                row[0]
                for row in c.execute(
                    "SELECT request_id FROM feature_request_votes WHERE voter_key=?", (voter,)
                )
            }
            rows = c.execute(
                f"""SELECT id, title, description, status, created_at, vote_count, seeded
                    FROM feature_requests ORDER BY {order} LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return _json({"sort": sort, "requests": [_public(row, row["id"] in voted) for row in rows]})
        finally:
            c.close()

    @application.post(
        "/api/requests",
        status_code=201,
        tags=["requests"],
        summary="Submit a feature request. email is optional and private. website is a honeypot and must be empty.",
    )
    async def create_request(request: Request):
        appmod.rate("request_submit", "ip:" + appmod.client_ip(request))
        try:
            data = await request.json()
        except Exception:
            data = None
        if not isinstance(data, dict):
            raise HTTPException(422, "Send a JSON object with title and description.")
        website = data.get("website")
        if website is not None and not isinstance(website, str):
            raise HTTPException(422, "Could not submit that request.")
        if str(website or "").strip():
            raise HTTPException(422, "Could not submit that request.")
        raw_title = data.get("title")
        raw_desc = data.get("description")
        if not isinstance(raw_title, str) or not isinstance(raw_desc, str):
            raise HTTPException(422, "Title and description must be text.")
        if len(raw_title) > TITLE_MAX or len(raw_desc) > DESC_MAX:
            raise HTTPException(
                422,
                f"Title must be {TITLE_MIN}–{TITLE_MAX} characters and description {DESC_MIN}–{DESC_MAX}.",
            )
        title, description = _prepare(raw_title, raw_desc)
        email = _email(data.get("email"))
        key = title_key(title)
        c = appmod.db()
        try:
            ensure(c)
            if c.execute("SELECT 1 FROM feature_requests WHERE title_key=?", (key,)).fetchone():
                raise HTTPException(409, "A request with that title already exists. Vote for it instead.")
            try:
                cur = c.execute(
                    """INSERT INTO feature_requests(
                           title, description, email, status, created_at, vote_count, seeded, title_key)
                       VALUES(?,?,?,'open',?,0,0,?)""",
                    (title, description, email, time.time(), key),
                )
                c.commit()
            except sqlite3.IntegrityError:
                c.rollback()
                raise HTTPException(409, "A request with that title already exists. Vote for it instead.")
            row = c.execute("SELECT * FROM feature_requests WHERE id=?", (cur.lastrowid,)).fetchone()
            return _json(_public(row, False), 201)
        finally:
            c.close()

    @application.post(
        "/api/requests/{rid}/vote",
        tags=["requests"],
        summary="Upvote once. Uses a bot Bearer key, a human session, or a salted IP and user-agent hash.",
    )
    def vote_request(
        rid: int,
        request: Request,
        authorization: Optional[str] = Header(None),
    ):
        appmod.rate("request_vote", "ip:" + appmod.client_ip(request))
        c = appmod.db()
        try:
            ensure(c)
            _load(c, rid)
            voter = identify(c, request, authorization, strict=True)
            try:
                c.execute(
                    "INSERT INTO feature_request_votes(request_id, voter_key, created_at) VALUES(?,?,?)",
                    (rid, voter, time.time()),
                )
                c.execute(
                    """UPDATE feature_requests SET vote_count=(
                           SELECT COUNT(*) FROM feature_request_votes WHERE request_id=?
                       ) WHERE id=?""",
                    (rid, rid),
                )
                c.commit()
            except sqlite3.IntegrityError:
                c.rollback()
                raise HTTPException(409, "You already voted on this request.")
            fresh = _load(c, rid)
            return _json({"ok": True, "id": rid, "vote_count": int(fresh["vote_count"]), "voted": True})
        finally:
            c.close()

    @application.get(
        "/api/admin/requests",
        tags=["admin"],
        summary="List feature requests including private emails (admin)",
    )
    def admin_list(_actor: dict = Depends(appmod.auth_admin)):
        c = appmod.db()
        try:
            ensure(c)
            rows = c.execute(
                """SELECT * FROM feature_requests
                   ORDER BY vote_count DESC, created_at DESC, id DESC LIMIT 500"""
            ).fetchall()
            return _json({"requests": [_admin(row) for row in rows]})
        finally:
            c.close()

    @application.post(
        "/api/admin/requests/{rid}/status",
        tags=["admin"],
        summary="Set a feature request status: open, planned, shipped, or declined (admin)",
    )
    async def admin_status(rid: int, request: Request, actor: dict = Depends(appmod.auth_admin)):
        try:
            data = await request.json()
        except Exception:
            data = None
        status = str((data or {}).get("status") or "") if isinstance(data, dict) else ""
        if status not in STATUSES:
            raise HTTPException(422, "status must be open, planned, shipped, or declined")
        c = appmod.db()
        try:
            ensure(c)
            _load(c, rid)
            c.execute("UPDATE feature_requests SET status=? WHERE id=?", (status, rid))
            c.commit()
            fresh = _load(c, rid)
        finally:
            c.close()
        appmod.audit_admin(actor, "request_status", target=str(rid), detail=status)
        return _json(_admin(fresh))

    @application.get("/requests", include_in_schema=False)
    def requests_page():
        return FileResponse(os.path.join(_STATIC, "requests.html"))

    @application.get("/requests.js", include_in_schema=False)
    def requests_js():
        return FileResponse(os.path.join(_STATIC, "requests.js"), media_type="text/javascript")

    @application.get("/admin/requests", include_in_schema=False)
    def admin_requests_page():
        return FileResponse(os.path.join(_STATIC, "admin-requests.html"))
