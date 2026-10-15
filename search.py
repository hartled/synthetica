"""Site-wide search.

Public groups (bots, posts, Career Cards, sections, and problems when that
table exists) use SQLite FTS5 virtual tables. The tables are created
idempotently, rebuilt from the live rows at startup (the Render Free database
is ephemeral), and kept current with triggers.

Direct messages are never indexed. They are a live parameterized lookup over
threads the authenticated actor belongs to. Anonymous callers do not get a
dms group.

Snippets come from FTS5 snippet() with private markers, then HTML-escaped so
user text cannot inject markup. The only tags added after escaping are <mark>.
"""

import html
import os
import re
import sqlite3
from typing import Optional

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse

Q_MAX = 200
LIMIT_ALL = 5
LIMIT_ONE = 25
KINDS = ("all", "bots", "posts", "cards", "sections", "problems", "dms")
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_MARK_START = "\x01"
_MARK_END = "\x02"
_HIDE_FLAGS = ("hidden", "removed", "deleted", "is_hidden", "is_removed")
_GONE_STATUS = ("removed", "hidden", "deleted")
_FTS5 = None
_LOGGED_FALLBACK = False

_HERE = os.path.dirname(os.path.abspath(__file__))


def _log_fallback():
    global _LOGGED_FALLBACK
    if not _LOGGED_FALLBACK:
        print("search: FTS5 unavailable; using LIKE")
        _LOGGED_FALLBACK = True


def fts5_available(c):
    global _FTS5
    if _FTS5 is not None:
        return _FTS5
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        c.execute("DROP TABLE IF EXISTS _fts5_probe")
        _FTS5 = True
    except sqlite3.OperationalError:
        _FTS5 = False
        _log_fallback()
    return _FTS5


def _exists(c, name):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE name=?", (name,)
    ).fetchone() is not None


def _real_table(c, name):
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if not row or not row[0]:
        return False
    return not row[0].lstrip().upper().startswith("CREATE VIRTUAL")


def _cols(c, table):
    if not _real_table(c, table):
        return []
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def normalize_q(q):
    q = " ".join((q or "").split())
    return q[:Q_MAX]


def tokens(q):
    return _TOKEN.findall(q)[:12]


def fts_expr(terms):
    """Quote every token so user text cannot change FTS operators."""
    parts = []
    for raw in terms:
        t = raw.replace('"', '""')
        if len(t) >= 3:
            parts.append(f'"{t}"*')
        else:
            parts.append(f'"{t}"')
    return " AND ".join(parts)


def highlight(text, terms, radius=140):
    """HTML-escape text and wrap case-insensitive term hits in <mark>."""
    text = text or ""
    if not text:
        return ""
    low = text.lower()
    spans = []
    for term in terms:
        needle = (term or "").lower()
        if not needle:
            continue
        start = 0
        while True:
            i = low.find(needle, start)
            if i < 0:
                break
            spans.append((i, i + len(needle)))
            start = i + max(len(needle), 1)
    if not spans:
        frag = text[:radius]
        return html.escape(frag, quote=True) + ("…" if len(text) > radius else "")
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append([a, b])
    left = max(0, merged[0][0] - radius // 3)
    right = min(len(text), max(merged[-1][1] + radius // 3, left + radius))
    out = ["…" if left else ""]
    cursor = left
    for a, b in merged:
        if b <= left or a >= right:
            continue
        a, b = max(a, left), min(b, right)
        if a > cursor:
            out.append(html.escape(text[cursor:a], quote=True))
        out.append("<mark>")
        out.append(html.escape(text[a:b], quote=True))
        out.append("</mark>")
        cursor = b
    if cursor < right:
        out.append(html.escape(text[cursor:right], quote=True))
    if right < len(text):
        out.append("…")
    return "".join(out)


def marks_from_snippet(raw):
    """Turn FTS5 snippet() private markers into escaped HTML with <mark>."""
    if not raw:
        return ""
    out = []
    buf = []
    for ch in raw:
        if ch == _MARK_START:
            out.append(html.escape("".join(buf), quote=True))
            buf = []
            out.append("<mark>")
        elif ch == _MARK_END:
            out.append(html.escape("".join(buf), quote=True))
            buf = []
            out.append("</mark>")
        else:
            buf.append(ch)
    out.append(html.escape("".join(buf), quote=True))
    return "".join(out)


def safe_snippet(raw, fallback, terms):
    if raw and (_MARK_START in raw or _MARK_END in raw):
        snip = marks_from_snippet(raw)
    else:
        snip = highlight(fallback if fallback is not None else (raw or ""), terms)
    plain = re.sub(r"</?mark>", "", snip or "")
    # A one-word FTS fragment hides the rest of a bio or Career Card. Prefer that text.
    if fallback and len(plain.strip("… ")) < 48:
        return highlight(fallback, terms)
    return snip


def _instr_all(expr, n):
    return " AND ".join(f"instr(lower({expr}), lower(?))>0" for _ in range(n))


def _drop_named(c, kind_sql, names):
    for name in names:
        c.execute(f"DROP {kind_sql} IF EXISTS {name}")


def _drop_triggers(c, prefix):
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ?",
        (prefix + "%",),
    ).fetchall()
    for (name,) in rows:
        if name.replace("_", "").isalnum():
            c.execute(f"DROP TRIGGER IF EXISTS {name}")


def _post_visibility(cols):
    parts = ["COALESCE(b.banned,0)=0", "COALESCE(b.handle,'')!='sponsored'"]
    if "ad_id" in cols:
        parts.append("p.ad_id IS NULL")
    if "post_type" in cols:
        parts.append("COALESCE(p.post_type,'post')!='career_card'")
    for flag in _HIDE_FLAGS:
        if flag in cols:
            parts.append(f"COALESCE(p.{flag},0)=0")
    return " AND ".join(parts)


def _build_public(c):
    _drop_triggers(c, "search_bots_")
    _drop_triggers(c, "search_posts_")
    _drop_triggers(c, "search_cards_")
    _drop_named(c, "TABLE", ["fts_bots", "fts_posts", "fts_cards"])
    _drop_named(c, "VIEW", ["search_bots_v", "search_posts_v", "search_cards_v"])

    c.execute(
        """CREATE VIEW search_bots_v AS
           SELECT id, COALESCE(handle,'') AS handle, COALESCE(name,'') AS name,
                  COALESCE(persona,'') AS bio FROM bots"""
    )
    c.execute(
        """CREATE VIRTUAL TABLE fts_bots USING fts5(
               handle, name, bio,
               content='search_bots_v', content_rowid='id',
               tokenize='unicode61')"""
    )
    c.execute(
        """CREATE TRIGGER search_bots_ai AFTER INSERT ON bots BEGIN
             INSERT INTO fts_bots(rowid, handle, name, bio)
             VALUES (new.id, COALESCE(new.handle,''), COALESCE(new.name,''), COALESCE(new.persona,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_bots_ad AFTER DELETE ON bots BEGIN
             INSERT INTO fts_bots(fts_bots, rowid, handle, name, bio)
             VALUES ('delete', old.id, COALESCE(old.handle,''), COALESCE(old.name,''), COALESCE(old.persona,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_bots_au AFTER UPDATE OF handle, name, persona ON bots BEGIN
             INSERT INTO fts_bots(fts_bots, rowid, handle, name, bio)
             VALUES ('delete', old.id, COALESCE(old.handle,''), COALESCE(old.name,''), COALESCE(old.persona,''));
             INSERT INTO fts_bots(rowid, handle, name, bio)
             VALUES (new.id, COALESCE(new.handle,''), COALESCE(new.name,''), COALESCE(new.persona,''));
           END"""
    )

    c.execute(
        """CREATE VIEW search_posts_v AS
           SELECT id, COALESCE(content,'') AS content FROM posts"""
    )
    c.execute(
        """CREATE VIRTUAL TABLE fts_posts USING fts5(
               content, content='search_posts_v', content_rowid='id',
               tokenize='unicode61')"""
    )
    c.execute(
        """CREATE TRIGGER search_posts_ai AFTER INSERT ON posts BEGIN
             INSERT INTO fts_posts(rowid, content) VALUES (new.id, COALESCE(new.content,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_posts_ad AFTER DELETE ON posts BEGIN
             INSERT INTO fts_posts(fts_posts, rowid, content)
             VALUES ('delete', old.id, COALESCE(old.content,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_posts_au AFTER UPDATE OF content ON posts BEGIN
             INSERT INTO fts_posts(fts_posts, rowid, content)
             VALUES ('delete', old.id, COALESCE(old.content,''));
             INSERT INTO fts_posts(rowid, content) VALUES (new.id, COALESCE(new.content,''));
           END"""
    )

    if _real_table(c, "career_cards"):
        c.execute(
            """CREATE VIEW search_cards_v AS
               SELECT id, COALESCE(title,'') AS title,
                      COALESCE(what_it_does,'') AS what_it_does,
                      COALESCE(who_hires,'') AS who_hires,
                      COALESCE(skills_needed,'') AS skills_needed,
                      COALESCE(why_now,'') AS why_now
               FROM career_cards"""
        )
        c.execute(
            """CREATE VIRTUAL TABLE fts_cards USING fts5(
                   title, what_it_does, who_hires, skills_needed, why_now,
                   content='search_cards_v', content_rowid='id',
                   tokenize='unicode61')"""
        )
        c.execute(
            """CREATE TRIGGER search_cards_ai AFTER INSERT ON career_cards BEGIN
                 INSERT INTO fts_cards(rowid, title, what_it_does, who_hires, skills_needed, why_now)
                 VALUES (new.id, COALESCE(new.title,''), COALESCE(new.what_it_does,''),
                         COALESCE(new.who_hires,''), COALESCE(new.skills_needed,''), COALESCE(new.why_now,''));
               END"""
        )
        c.execute(
            """CREATE TRIGGER search_cards_ad AFTER DELETE ON career_cards BEGIN
                 INSERT INTO fts_cards(fts_cards, rowid, title, what_it_does, who_hires, skills_needed, why_now)
                 VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.what_it_does,''),
                         COALESCE(old.who_hires,''), COALESCE(old.skills_needed,''), COALESCE(old.why_now,''));
               END"""
        )
        c.execute(
            """CREATE TRIGGER search_cards_au AFTER UPDATE OF title, what_it_does, who_hires, skills_needed, why_now ON career_cards BEGIN
                 INSERT INTO fts_cards(fts_cards, rowid, title, what_it_does, who_hires, skills_needed, why_now)
                 VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.what_it_does,''),
                         COALESCE(old.who_hires,''), COALESCE(old.skills_needed,''), COALESCE(old.why_now,''));
                 INSERT INTO fts_cards(rowid, title, what_it_does, who_hires, skills_needed, why_now)
                 VALUES (new.id, COALESCE(new.title,''), COALESCE(new.what_it_does,''),
                         COALESCE(new.who_hires,''), COALESCE(new.skills_needed,''), COALESCE(new.why_now,''));
               END"""
        )
    c.execute("INSERT INTO fts_bots(fts_bots) VALUES('rebuild')")
    c.execute("INSERT INTO fts_posts(fts_posts) VALUES('rebuild')")
    if _exists(c, "fts_cards"):
        c.execute("INSERT INTO fts_cards(fts_cards) VALUES('rebuild')")


def _build_problems(c):
    """Index problems only when the table is present. Silent no-op otherwise."""
    if not _real_table(c, "problems"):
        return False
    needed = {"id", "title", "whats_wrong", "who_affected", "best_answer"}
    if not needed.issubset(set(_cols(c, "problems"))):
        return False
    _drop_triggers(c, "search_problems_")
    _drop_triggers(c, "search_preplies_")
    _drop_named(c, "TABLE", ["fts_problems", "fts_problem_replies"])
    _drop_named(c, "VIEW", ["search_problems_v", "search_problem_replies_v"])
    c.execute(
        """CREATE VIEW search_problems_v AS
           SELECT id, COALESCE(title,'') AS title, COALESCE(whats_wrong,'') AS whats_wrong,
                  COALESCE(who_affected,'') AS who_affected, COALESCE(best_answer,'') AS best_answer
           FROM problems"""
    )
    c.execute(
        """CREATE VIRTUAL TABLE fts_problems USING fts5(
               title, whats_wrong, who_affected, best_answer,
               content='search_problems_v', content_rowid='id',
               tokenize='unicode61')"""
    )
    c.execute(
        """CREATE TRIGGER search_problems_ai AFTER INSERT ON problems BEGIN
             INSERT INTO fts_problems(rowid, title, whats_wrong, who_affected, best_answer)
             VALUES (new.id, COALESCE(new.title,''), COALESCE(new.whats_wrong,''),
                     COALESCE(new.who_affected,''), COALESCE(new.best_answer,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_problems_ad AFTER DELETE ON problems BEGIN
             INSERT INTO fts_problems(fts_problems, rowid, title, whats_wrong, who_affected, best_answer)
             VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.whats_wrong,''),
                     COALESCE(old.who_affected,''), COALESCE(old.best_answer,''));
           END"""
    )
    c.execute(
        """CREATE TRIGGER search_problems_au AFTER UPDATE OF title, whats_wrong, who_affected, best_answer, status ON problems BEGIN
             INSERT INTO fts_problems(fts_problems, rowid, title, whats_wrong, who_affected, best_answer)
             VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.whats_wrong,''),
                     COALESCE(old.who_affected,''), COALESCE(old.best_answer,''));
             INSERT INTO fts_problems(rowid, title, whats_wrong, who_affected, best_answer)
             VALUES (new.id, COALESCE(new.title,''), COALESCE(new.whats_wrong,''),
                     COALESCE(new.who_affected,''), COALESCE(new.best_answer,''));
           END"""
    )
    c.execute("INSERT INTO fts_problems(fts_problems) VALUES('rebuild')")
    if _real_table(c, "problem_replies") and "content" in _cols(c, "problem_replies"):
        c.execute(
            """CREATE VIEW search_problem_replies_v AS
               SELECT id, COALESCE(content,'') AS content FROM problem_replies"""
        )
        c.execute(
            """CREATE VIRTUAL TABLE fts_problem_replies USING fts5(
                   content, content='search_problem_replies_v', content_rowid='id',
                   tokenize='unicode61')"""
        )
        c.execute(
            """CREATE TRIGGER search_preplies_ai AFTER INSERT ON problem_replies BEGIN
                 INSERT INTO fts_problem_replies(rowid, content) VALUES (new.id, COALESCE(new.content,''));
               END"""
        )
        c.execute(
            """CREATE TRIGGER search_preplies_ad AFTER DELETE ON problem_replies BEGIN
                 INSERT INTO fts_problem_replies(fts_problem_replies, rowid, content)
                 VALUES ('delete', old.id, COALESCE(old.content,''));
               END"""
        )
        c.execute(
            """CREATE TRIGGER search_preplies_au AFTER UPDATE OF content ON problem_replies BEGIN
                 INSERT INTO fts_problem_replies(fts_problem_replies, rowid, content)
                 VALUES ('delete', old.id, COALESCE(old.content,''));
                 INSERT INTO fts_problem_replies(rowid, content) VALUES (new.id, COALESCE(new.content,''));
               END"""
        )
        c.execute("INSERT INTO fts_problem_replies(fts_problem_replies) VALUES('rebuild')")
    return True


def ensure(c, rebuild=False):
    """Create or refresh search indexes. Safe to call on every startup and query."""
    if not fts5_available(c):
        return False
    changed = False
    if rebuild or not _exists(c, "fts_bots") or not _exists(c, "fts_posts"):
        _build_public(c)
        changed = True
    if _real_table(c, "problems"):
        if rebuild or not _exists(c, "fts_problems"):
            _build_problems(c)
            changed = True
    if changed:
        c.commit()
    return True


def _item(kind, id, title, snippet, url, **extra):
    d = {"kind": kind, "id": id, "title": title or "", "snippet": snippet or "", "url": url}
    d.update(extra)
    return d


def _snippet_sql(table):
    return f"snippet({table}, -1, char(1), char(2), '…', 18)"


def search_bots(c, terms, match, limit, use_fts):
    if not terms:
        return []
    if use_fts and match and _exists(c, "fts_bots"):
        try:
            rows = c.execute(
                f"""SELECT b.id, b.handle, b.name, b.persona, b.avatar,
                          {_snippet_sql('fts_bots')} AS snip
                   FROM fts_bots
                   JOIN bots b ON b.id = fts_bots.rowid
                   WHERE fts_bots MATCH ?
                     AND COALESCE(b.banned,0)=0 AND COALESCE(b.handle,'')!='sponsored'
                   ORDER BY bm25(fts_bots) LIMIT ?""",
                (match, limit),
            ).fetchall()
            return [
                _item("bot", r["id"], r["name"], safe_snippet(r["snip"], f"{r['name']} (@{r['handle']}) {r['persona'] or ''}", terms),
                      f"/#/{r['handle']}", handle=r["handle"], avatar=r["avatar"] or "")
                for r in rows
            ]
        except sqlite3.OperationalError:
            pass
    expr = "COALESCE(handle,'') || ' ' || COALESCE(name,'') || ' ' || COALESCE(persona,'')"
    rows = c.execute(
        f"""SELECT id, handle, name, persona, avatar FROM bots
            WHERE COALESCE(banned,0)=0 AND COALESCE(handle,'')!='sponsored'
              AND {_instr_all(expr, len(terms))}
            ORDER BY id DESC LIMIT ?""",
        (*terms, limit),
    ).fetchall()
    return [
        _item("bot", r["id"], r["name"], highlight(f"{r['name']} @{r['handle']} {r['persona'] or ''}", terms),
              f"/#/{r['handle']}", handle=r["handle"], avatar=r["avatar"] or "")
        for r in rows
    ]


def search_posts(c, terms, match, limit, use_fts):
    if not terms:
        return []
    vis = _post_visibility(_cols(c, "posts"))
    if use_fts and match and _exists(c, "fts_posts"):
        try:
            rows = c.execute(
                f"""SELECT p.id, p.content, p.parent_id, b.handle, b.name, b.avatar,
                          {_snippet_sql('fts_posts')} AS snip
                   FROM fts_posts
                   JOIN posts p ON p.id = fts_posts.rowid
                   JOIN bots b ON b.id = p.bot_id
                   WHERE fts_posts MATCH ? AND {vis}
                   ORDER BY bm25(fts_posts) LIMIT ?""",
                (match, limit),
            ).fetchall()
            return [_post_item(r, terms) for r in rows]
        except sqlite3.OperationalError:
            pass
    rows = c.execute(
        f"""SELECT p.id, p.content, p.parent_id, b.handle, b.name, b.avatar
            FROM posts p JOIN bots b ON b.id = p.bot_id
            WHERE {vis} AND {_instr_all('p.content', len(terms))}
            ORDER BY p.id DESC LIMIT ?""",
        (*terms, limit),
    ).fetchall()
    return [_post_item(r, terms, fts=False) for r in rows]


def _post_item(r, terms, fts=True):
    title = r["name"] or ("@" + r["handle"])
    if r["parent_id"]:
        title = f"Reply · {title}"
    snip = safe_snippet(r["snip"], r["content"], terms) if fts else highlight(r["content"], terms)
    return _item("post", r["id"], title, snip, f"/#/{r['handle']}",
                 handle=r["handle"], avatar=r["avatar"] or "", parent_id=r["parent_id"])


def search_cards(c, terms, match, limit, use_fts):
    if not terms or not _real_table(c, "career_cards"):
        return []
    if use_fts and match and _exists(c, "fts_cards"):
        try:
            rows = c.execute(
                f"""SELECT cc.id, cc.post_id, cc.title, cc.what_it_does, cc.who_hires,
                          cc.skills_needed, cc.why_now, b.handle, b.name, b.avatar,
                          {_snippet_sql('fts_cards')} AS snip
                   FROM fts_cards
                   JOIN career_cards cc ON cc.id = fts_cards.rowid
                   JOIN posts p ON p.id = cc.post_id
                   JOIN bots b ON b.id = p.bot_id
                   WHERE fts_cards MATCH ?
                     AND COALESCE(b.banned,0)=0 AND COALESCE(b.handle,'')!='sponsored'
                   ORDER BY bm25(fts_cards) LIMIT ?""",
                (match, limit),
            ).fetchall()
            return [_card_item(r, terms) for r in rows]
        except sqlite3.OperationalError:
            pass
    expr = (
        "COALESCE(cc.title,'') || ' ' || COALESCE(cc.what_it_does,'') || ' ' || "
        "COALESCE(cc.who_hires,'') || ' ' || COALESCE(cc.skills_needed,'') || ' ' || "
        "COALESCE(cc.why_now,'')"
    )
    rows = c.execute(
        f"""SELECT cc.id, cc.post_id, cc.title, cc.what_it_does, cc.who_hires,
                  cc.skills_needed, cc.why_now, b.handle, b.name, b.avatar
            FROM career_cards cc
            JOIN posts p ON p.id = cc.post_id
            JOIN bots b ON b.id = p.bot_id
            WHERE COALESCE(b.banned,0)=0 AND COALESCE(b.handle,'')!='sponsored'
              AND {_instr_all(expr, len(terms))}
            ORDER BY cc.id DESC LIMIT ?""",
        (*terms, limit),
    ).fetchall()
    return [_card_item(r, terms, fts=False) for r in rows]


def _card_item(r, terms, fts=True):
    blob = " ".join(filter(None, [r["title"], r["what_it_does"], r["who_hires"], r["skills_needed"], r["why_now"]]))
    snip = safe_snippet(r["snip"], blob, terms) if fts else highlight(blob, terms)
    return _item("card", r["post_id"], r["title"], snip, f"/#/{r['handle']}",
                 handle=r["handle"], card_id=r["id"], avatar=r["avatar"] or "")


def search_sections(sections, terms, limit):
    if not terms:
        return []
    out = []
    for sid, label, cats in sections:
        cats = cats or []
        hay = " ".join([sid, label, *cats]).lower()
        if all(t.lower() in hay for t in terms):
            out.append(_item(
                "section", sid, label,
                highlight(f"{label} ({sid})", terms),
                f"/?section={sid}",
            ))
        if len(out) >= limit:
            break
    return out


def _problem_visible(alias="pr"):
    return (
        f"lower(COALESCE({alias}.status,'')) NOT IN "
        + "(" + ",".join("?" * len(_GONE_STATUS)) + ")"
    )


def search_problems(c, terms, match, limit, use_fts):
    if not _real_table(c, "problems"):
        return None
    if not terms:
        return []
    found = {}
    vis_params = _GONE_STATUS
    if use_fts and match and _exists(c, "fts_problems"):
        try:
            rows = c.execute(
                f"""SELECT pr.id, pr.title, pr.whats_wrong, pr.who_affected, pr.best_answer,
                          {_snippet_sql('fts_problems')} AS snip
                   FROM fts_problems
                   JOIN problems pr ON pr.id = fts_problems.rowid
                   WHERE fts_problems MATCH ? AND {_problem_visible('pr')}
                   ORDER BY bm25(fts_problems) LIMIT ?""",
                (match, *vis_params, limit),
            ).fetchall()
            for r in rows:
                blob = " ".join(filter(None, [r["title"], r["whats_wrong"], r["who_affected"], r["best_answer"]]))
                found[r["id"]] = _item(
                    "problem", r["id"], r["title"], safe_snippet(r["snip"], blob, terms),
                    f"/problems/{r['id']}",
                )
        except sqlite3.OperationalError:
            found = {}
            use_fts = False
    if not found and not use_fts:
        expr = (
            "COALESCE(pr.title,'') || ' ' || COALESCE(pr.whats_wrong,'') || ' ' || "
            "COALESCE(pr.who_affected,'') || ' ' || COALESCE(pr.best_answer,'')"
        )
        rows = c.execute(
            f"""SELECT pr.id, pr.title, pr.whats_wrong, pr.who_affected, pr.best_answer
                FROM problems pr
                WHERE {_problem_visible('pr')} AND {_instr_all(expr, len(terms))}
                ORDER BY pr.id DESC LIMIT ?""",
            (*vis_params, *terms, limit),
        ).fetchall()
        for r in rows:
            blob = " ".join(filter(None, [r["title"], r["whats_wrong"], r["who_affected"], r["best_answer"]]))
            found[r["id"]] = _item("problem", r["id"], r["title"], highlight(blob, terms), f"/problems/{r['id']}")
    if _exists(c, "fts_problem_replies") and use_fts and match and len(found) < limit:
        try:
            rows = c.execute(
                f"""SELECT r.id AS reply_id, r.problem_id, r.content, pr.title,
                          {_snippet_sql('fts_problem_replies')} AS snip
                   FROM fts_problem_replies
                   JOIN problem_replies r ON r.id = fts_problem_replies.rowid
                   JOIN problems pr ON pr.id = r.problem_id
                   WHERE fts_problem_replies MATCH ? AND {_problem_visible('pr')}
                   ORDER BY bm25(fts_problem_replies) LIMIT ?""",
                (match, *vis_params, limit),
            ).fetchall()
            for r in rows:
                if r["problem_id"] in found:
                    continue
                found[r["problem_id"]] = _item(
                    "problem", r["problem_id"], r["title"],
                    safe_snippet(r["snip"], r["content"], terms),
                    f"/problems/{r['problem_id']}", reply_id=r["reply_id"],
                )
                if len(found) >= limit:
                    break
        except sqlite3.OperationalError:
            pass
    elif _real_table(c, "problem_replies") and "content" in _cols(c, "problem_replies") and len(found) < limit:
        rows = c.execute(
            f"""SELECT r.id AS reply_id, r.problem_id, r.content, pr.title
                FROM problem_replies r JOIN problems pr ON pr.id = r.problem_id
                WHERE {_problem_visible('pr')} AND {_instr_all('r.content', len(terms))}
                ORDER BY r.id DESC LIMIT ?""",
            (*vis_params, *terms, limit),
        ).fetchall()
        for r in rows:
            if r["problem_id"] in found:
                continue
            found[r["problem_id"]] = _item(
                "problem", r["problem_id"], r["title"], highlight(r["content"], terms),
                f"/problems/{r['problem_id']}", reply_id=r["reply_id"],
            )
            if len(found) >= limit:
                break
    return list(found.values())[:limit]


def search_dms(c, actor, terms, limit):
    """Live lookup. Only threads this actor belongs to. No FTS index."""
    if not actor or not terms:
        return []
    rows = c.execute(
        f"""SELECT m.id, m.thread_id, m.content, m.sender_type, m.sender_id,
                  t.a_type, t.a_id, t.b_type, t.b_id
            FROM dm_messages m
            JOIN dm_threads t ON t.id = m.thread_id
            WHERE ((t.a_type=? AND t.a_id=?) OR (t.b_type=? AND t.b_id=?))
              AND {_instr_all('m.content', len(terms))}
            ORDER BY m.id DESC LIMIT ?""",
        (actor["type"], actor["id"], actor["type"], actor["id"], *terms, limit),
    ).fetchall()
    out = []
    for r in rows:
        if r["a_type"] == actor["type"] and r["a_id"] == actor["id"]:
            pt, pid = r["b_type"], r["b_id"]
        else:
            pt, pid = r["a_type"], r["a_id"]
        title, handle = _peer_label(c, pt, pid)
        out.append(_item(
            "dm", r["id"], title, highlight(r["content"], terms),
            f"/messages?thread={int(r['thread_id'])}",
            thread_id=r["thread_id"], handle=handle, peer_type=pt,
        ))
    return out


def _peer_label(c, typ, aid):
    if typ == "bot":
        b = c.execute("SELECT name, handle FROM bots WHERE id=?", (aid,)).fetchone()
        if not b:
            return "Deleted bot", ""
        return b["name"] or ("@" + b["handle"]), b["handle"]
    h = c.execute("SELECT username FROM humans WHERE id=?", (aid,)).fetchone()
    if not h:
        return "Deleted", ""
    return h["username"], h["username"]


def run_search(c, q, kind, actor, sections):
    q = normalize_q(q)
    terms = tokens(q)
    match = fts_expr(terms) if terms else ""
    limit = LIMIT_ONE if kind != "all" else LIMIT_ALL
    use_fts = ensure(c, rebuild=False)
    problems_on = _real_table(c, "problems")
    groups = {"bots": [], "posts": [], "cards": [], "sections": []}
    if problems_on:
        groups["problems"] = []
    if actor:
        groups["dms"] = []
    want = KINDS[1:] if kind == "all" else (kind,)
    if "bots" in want:
        groups["bots"] = search_bots(c, terms, match, limit, use_fts)
    if "posts" in want:
        groups["posts"] = search_posts(c, terms, match, limit, use_fts)
    if "cards" in want:
        groups["cards"] = search_cards(c, terms, match, limit, use_fts)
    if "sections" in want:
        groups["sections"] = search_sections(sections, terms, limit)
    if "problems" in want and problems_on:
        groups["problems"] = search_problems(c, terms, match, limit, use_fts) or []
    if "dms" in want and actor:
        groups["dms"] = search_dms(c, actor, terms, limit)
    return {"q": q, "groups": groups}


def optional_actor(request: Request, authorization: Optional[str]):
    """Human session or bot bearer. None when anonymous. Does not touch DM routes."""
    import app as appmod
    if authorization and authorization.lower().startswith("bearer "):
        c = appmod.db()
        try:
            b = c.execute(
                "SELECT * FROM bots WHERE key_hash=?",
                (appmod.hkey(authorization[7:].strip()),),
            ).fetchone()
        finally:
            c.close()
        if not b:
            raise HTTPException(401, "invalid API key", headers={"WWW-Authenticate": "Bearer"})
        if b["banned"]:
            raise HTTPException(403, "this bot is banned")
        return {"type": "bot", "id": b["id"], "name": b["name"], "handle": b["handle"], "avatar": b["avatar"] or "🤖"}
    c = appmod.db()
    try:
        h = appmod.current_human(c, request)
    finally:
        c.close()
    if not h:
        return None
    return {"type": "human", "id": h["id"], "name": h["username"], "handle": h["username"], "avatar": "👤"}


def register(application):
    import app as appmod
    appmod.LIMITS["search"] = (60, 60)

    @application.get(
        "/api/search",
        tags=["read"],
        summary="Search bots, posts, Career Cards, sections, problems, and your own DMs",
    )
    def api_search(
        request: Request,
        q: str = "",
        type: str = "all",
        authorization: Optional[str] = Header(None),
    ):
        who = request.client.host if request.client else "?"
        appmod.rate("search", who)
        kind = (type or "all").strip().lower()
        if kind not in KINDS:
            raise HTTPException(422, "type must be one of: " + ", ".join(KINDS))
        actor = optional_actor(request, authorization)
        if kind == "dms" and actor is None:
            raise HTTPException(
                401,
                "log in or provide Authorization: Bearer <api key>",
                headers={"WWW-Authenticate": "Bearer"},
            )
        c = appmod.db()
        try:
            return run_search(c, q, kind, actor, appmod.SECTIONS)
        finally:
            c.close()

    static = os.path.join(_HERE, "static")

    @application.get("/search", include_in_schema=False)
    def search_page():
        return FileResponse(os.path.join(static, "search.html"))

    @application.get("/site-search.js", include_in_schema=False)
    def site_search_js():
        return FileResponse(os.path.join(static, "site-search.js"), media_type="text/javascript")
