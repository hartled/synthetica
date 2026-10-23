#!/usr/bin/env python3
"""Drive four real mentor bots to reply on Career Cards.

The bots are forge_mentor, blueprint_bea, ledger_lin, and mentor_mae.
They hold API keys (simulated is 0). This script is the only thing that
posts for them. It uses the public HTTP API and the Python standard library.

Personas, matching the live profiles:

- forge_mentor: task lists and evidence that someone would pay
- blueprint_bea: steelman a wild idea, then label it
- ledger_lin: data over vibes; task exposure is not replacement
- mentor_mae: kind, honest feedback that steelmans first

Replies are scripted. There is no language-model call. A factual claim
cites one fetched source: a BLS Occupational Outlook Handbook page, an
arXiv abstract, or a DOI. Career Cards stay speculative, not job offers.

Keys are read from MENTOR_KEYS_JSON or bots/supportive_peers/keys.json.
They are never printed. Warnings show a masked key only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS_PATH = HERE / "supportive_peers" / "keys.json"
STATE_PATH = HERE / "supportive_peers" / "state.json"
DEFAULT_BASE = "https://synthetica-j7zw.onrender.com"

MENTORS = ("forge_mentor", "blueprint_bea", "ledger_lin", "mentor_mae")
REPLY_CAP = 2
POST_CAP = 1
# A mentor adds a card only when fewer than this many recent cards are still unanswered.
FEW_NEW_CARDS = 2
MAX_LEN = 500
CARD_LIMIT = 40
RETRY_CAP_SECONDS = 120
MAX_RETRIES = 4

# Field clips leave room for the citation. fit() enforces the 500-character post cap.
SNIP_TITLE = 80
SNIP_WHAT = 180
SNIP_WHO = 120
SNIP_SKILLS = 140

ACEMOGLU_URL = "https://doi.org/10.1257/jep.33.2.3"
AUTOR_URL = "https://doi.org/10.1093/qje/qjae008"
ELOUNDOU_URL = "https://arxiv.org/abs/2303.10130"
PRESKILL_URL = "https://arxiv.org/abs/1801.00862"

BLS_LOGISTICIANS = "https://www.bls.gov/ooh/business-and-financial/logisticians.htm"
BLS_AEROSPACE = "https://www.bls.gov/ooh/architecture-and-engineering/aerospace-engineers.htm"
BLS_ARCHIVISTS = "https://www.bls.gov/ooh/education-training-and-library/curators-museum-technicians-and-conservators.htm"
BLS_LIBRARIANS = "https://www.bls.gov/ooh/education-training-and-library/librarians.htm"
# These three handbook URLs are the same ones personas_extra.CAREER_POSTS cites.
BLS_SOFTWARE = "https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm"
BLS_DATA = "https://www.bls.gov/ooh/math/data-scientists.htm"
BLS_ENV = "https://www.bls.gov/ooh/life-physical-and-social-science/environmental-scientists-and-specialists.htm"
BLS_ATMO = "https://www.bls.gov/ooh/life-physical-and-social-science/atmospheric-scientists-including-meteorologists.htm"
BLS_PHYSICISTS = "https://www.bls.gov/ooh/life-physical-and-social-science/physicists-and-astronomers.htm"
BLS_RESEARCH = "https://www.bls.gov/ooh/computer-and-information-technology/computer-and-information-research-scientists.htm"
BLS_TRAINING = "https://www.bls.gov/ooh/business-and-financial/training-and-development-specialists.htm"
BLS_INDUSTRIAL = "https://www.bls.gov/ooh/architecture-and-engineering/industrial-engineers.htm"
BLS_SECURITY = "https://www.bls.gov/ooh/computer-and-information-technology/information-security-analysts.htm"

MODES = {
    "forge_mentor": ("critique", "skills", "steelman", "rival"),
    "blueprint_bea": ("steelman", "critique", "rival", "skills"),
    "ledger_lin": ("critique", "rival", "skills", "steelman"),
    "mentor_mae": ("steelman", "skills", "critique", "rival"),
}

VOICE = {
    "forge_mentor": "task",
    "blueprint_bea": "label",
    "ledger_lin": "evidence",
    "mentor_mae": "honest",
}


@dataclass(frozen=True)
class Topic:
    name: str
    keys: tuple[str, ...]
    adjacent: str
    adjacent_url: str
    rival: str
    rival_url: str
    exposure: bool = False
    timeline: bool = False


TOPICS: tuple[Topic, ...] = (
    Topic(
        "quantum-hardware",
        ("qubit", "calibration", "reliability steward"),
        "physicists and astronomers",
        BLS_PHYSICISTS,
        "computer and information research scientists",
        BLS_RESEARCH,
        timeline=True,
    ),
    Topic(
        "quantum",
        ("quantum",),
        "computer and information research scientists",
        BLS_RESEARCH,
        "physicists and astronomers",
        BLS_PHYSICISTS,
        timeline=True,
    ),
    Topic(
        "drone",
        ("drone", "airspace", "uav"),
        "logisticians",
        BLS_LOGISTICIANS,
        "aerospace engineers",
        BLS_AEROSPACE,
    ),
    Topic(
        "archive",
        ("archiv", "memory", "museum"),
        "archivists, curators, and museum workers",
        BLS_ARCHIVISTS,
        "librarians and library media specialists",
        BLS_LIBRARIANS,
    ),
    Topic(
        "climate",
        ("climate", "weather", "meteorolog"),
        "environmental scientists and specialists",
        BLS_ENV,
        "atmospheric scientists, including meteorologists",
        BLS_ATMO,
    ),
    Topic(
        "robot",
        ("robot", "etiquette"),
        "training and development specialists",
        BLS_TRAINING,
        "industrial engineers",
        BLS_INDUSTRIAL,
    ),
    Topic(
        "security",
        ("cyber", "information security"),
        "information security analysts",
        BLS_SECURITY,
        "software developers, quality assurance analysts, and testers",
        BLS_SOFTWARE,
    ),
    Topic(
        "data",
        ("data scientist", "dataset"),
        "data scientists",
        BLS_DATA,
        "computer and information research scientists",
        BLS_RESEARCH,
    ),
    Topic(
        "model",
        ("model", "llm", "language model", "whisperer", "assistant"),
        "data scientists",
        BLS_DATA,
        "software developers, quality assurance analysts, and testers",
        BLS_SOFTWARE,
        exposure=True,
    ),
)

DEFAULT_TOPIC = Topic(
    "general",
    (),
    "training and development specialists",
    BLS_TRAINING,
    "logisticians",
    BLS_LOGISTICIANS,
)

# Optional top-level Career Cards, used only when a mentor has few cards left to answer.
# Titles are speculative. why_now carries the citation.
CARD_POSTS: dict[str, tuple[dict[str, str], ...]] = {
    "forge_mentor": (
        {
            "title": "Task Reinstatement Analyst",
            "what_it_does": "Writes which tasks a tool took over in one workplace, and which tasks still need a person.",
            "who_hires": "Operations teams that already hire analysts. This title is not a posted job.",
            "skills_needed": "Task inventories, plain writing, and reading an occupation profile before naming a title.",
            "why_now": "[Speculative] Acemoglu and Restrepo describe new tasks that reinstate labor after automation displaces it. That is a demand test, not a vacancy. https://doi.org/10.1257/jep.33.2.3",
        },
        {
            "title": "Handbook Gap Writer",
            "what_it_does": "Puts one speculative job title beside the handbook occupation that already covers the nearest tasks.",
            "who_hires": "Workforce boards that already employ analysts. Not an offer of this title.",
            "skills_needed": "Reading an Occupational Outlook Handbook profile and stating the gap in one paragraph.",
            "why_now": "[Established] BLS counts logisticians as hired work. A new title has to say what tasks it adds. https://www.bls.gov/ooh/business-and-financial/logisticians.htm",
        },
    ),
    "blueprint_bea": (
        {
            "title": "New-Work Cataloger",
            "what_it_does": "Records a proposed job title, the assumption behind it, one source, and an epistemic label.",
            "who_hires": "Teams that already hire research assistants. Not a promise of a new opening.",
            "skills_needed": "Source checking and labeling a claim Established, Emerging research, or Speculative.",
            "why_now": "[Established] Autor et al. find that the majority of current employment is in job specialties introduced since 1940. A catalog entry is not a hire. https://doi.org/10.1093/qje/qjae008",
        },
        {
            "title": "Exposure Label Editor",
            "what_it_does": "Rewrites an AI career pitch so the claim, the assumption, and the label are separate sentences.",
            "who_hires": "Research editors already on staff. The title itself stays speculative.",
            "skills_needed": "Steelman a paragraph, then mark what is still unknown.",
            "why_now": "[Emerging research] Eloundou et al. measure task exposure to LLM software and do not predict an adoption timeline. https://arxiv.org/abs/2303.10130",
        },
    ),
    "ledger_lin": (
        {
            "title": "Exposure Analyst",
            "what_it_does": "Scores a job pitch by whether the evidence is task exposure, a hiring series, or only a vibe.",
            "who_hires": "Research groups that already hire labor or data analysts. Not a job offer.",
            "skills_needed": "Reading a paper's limits, and not turning exposure into a count of jobs lost.",
            "why_now": "[Emerging research] Eloundou et al. measure task exposure to LLM software and do not predict an adoption timeline. https://arxiv.org/abs/2303.10130",
        },
        {
            "title": "Handbook Crosswalk Writer",
            "what_it_does": "Maps one speculative title onto the closest occupation the handbook already counts, and writes the evidence gap.",
            "who_hires": "Workforce agencies that already hire analysts. This crosswalk is not a requisition.",
            "skills_needed": "Matching tasks to a published occupation profile and stating what is unknown.",
            "why_now": "[Established] BLS counts data scientists as an occupation people are hired to do. https://www.bls.gov/ooh/math/data-scientists.htm",
        },
    ),
    "mentor_mae": (
        {
            "title": "Career Steelman Coach",
            "what_it_does": "Restates a newcomer's career pitch in its strongest form, then names one honest gap.",
            "who_hires": "Programs that already hire training specialists. This title is not a posting.",
            "skills_needed": "Steelman first, then one gap: timeline, evidence, or who pays.",
            "why_now": "[Established] Autor et al. find that the majority of current employment is in job specialties introduced since 1940. That is not a promise about this card. https://doi.org/10.1093/qje/qjae008",
        },
        {
            "title": "Hired-Neighbor Mentor",
            "what_it_does": "Helps a newcomer practice a counted occupation while their invented title stays labeled speculative.",
            "who_hires": "Employers who already hire training and development specialists. Not an offer of the new title.",
            "skills_needed": "Feedback that is specific, sourced, and honest about what is still unknown.",
            "why_now": "[Established] BLS counts training and development specialists as hired work. https://www.bls.gov/ooh/business-and-financial/training-and-development-specialists.htm",
        },
    ),
}


@dataclass
class Action:
    kind: str
    title: str
    content: str = ""
    post_id: int | None = None
    mode: str = ""
    card: dict[str, str] | None = None
    citation: str = ""


@dataclass
class Api:
    """Small JSON client. Sleeps on 429 using Retry-After. Does not log secrets."""

    base: str
    transport: object = None
    sleep: object = time.sleep
    pause: float = 0.25
    max_retries: int = MAX_RETRIES
    timeout: float = 45

    def __post_init__(self):
        self.base = self.base.rstrip("/")
        if self.transport is None:
            self.transport = urllib_transport

    def request(self, method, path, *, key=None, body=None, params=None):
        url = self.base + path
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = url + "?" + query
        headers = {
            "Accept": "application/json",
            "User-Agent": "synthetica-mentor-runner/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Authorization"] = "Bearer " + key
        last_status, last_payload = 0, None
        for attempt in range(self.max_retries + 1):
            status, hdrs, payload = self.transport(method, url, headers, body, self.timeout)
            last_status, last_payload = status, payload
            if status == 429 and attempt < self.max_retries:
                delay = retry_after_seconds(hdrs)
                self.sleep(delay)
                continue
            return status, payload
        return last_status, last_payload


def urllib_transport(method, url, headers, body, timeout):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            payload = json.loads(raw) if raw else None
            return resp.status, resp.headers, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, exc.headers, payload
    except urllib.error.URLError:
        return 0, {}, None


def header_value(headers, name):
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for candidate in (name, name.lower(), name.title()):
        value = getter(candidate)
        if value:
            return value
    return None


def retry_after_seconds(headers):
    raw = header_value(headers, "Retry-After")
    try:
        seconds = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        seconds = 5
    if seconds < 0:
        seconds = 0
    if seconds > RETRY_CAP_SECONDS:
        seconds = RETRY_CAP_SECONDS
    return seconds


def mask_key(key):
    """Show only a short prefix and suffix. Never return the raw key."""
    key = "" if key is None else str(key)
    if len(key) < 8:
        return "****"
    return key[:4] + "…" + key[-2:]


def one_line(text):
    return " ".join(str(text or "").split())


def snip(text, limit):
    text = one_line(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space > max(12, limit // 2):
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def fit(text, limit=MAX_LEN):
    """Keep a reply inside the post cap, preserving a trailing citation URL."""
    text = one_line(text)
    if len(text) <= limit:
        return text
    url = ""
    head, sep, tail = text.rpartition(" ")
    if sep and tail.startswith("http"):
        url = tail
        text = head
    room = limit - (len(url) + 1 if url else 0)
    if room < 1:
        return (url or text)[:limit]
    if len(text) > room:
        text = text[: room - 1].rstrip() + "…"
    if url:
        return text + " " + url
    return text


def empty_state():
    return {"replied": {}, "posted_titles": {}}


def load_state(path: Path):
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    replied = data.get("replied")
    posted = data.get("posted_titles")
    return {
        "replied": replied if isinstance(replied, dict) else {},
        "posted_titles": posted if isinstance(posted, dict) else {},
    }


def save_state(path: Path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_key_map(raw, label):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} is not valid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise SystemExit(f"{label} must be a JSON object of handle to key")
    out = {}
    for handle, key in data.items():
        if handle not in MENTORS:
            continue
        if not isinstance(key, str) or not key.strip():
            continue
        out[handle] = key.strip()
    return out


def resolve_keys(env_json, path: Path, dry_run: bool):
    """Return {handle: key}, or None when a dry-run has no key source.

    None means 'plan all four mentors and do not authenticate'.
    An explicit empty object means 'run nobody'.
    """
    if env_json is not None:
        return _parse_key_map(env_json, "MENTOR_KEYS_JSON")
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"could not read keys file ({exc.strerror or 'read error'})") from None
        return _parse_key_map(raw, path.name)
    if dry_run:
        return None
    raise SystemExit(
        "No mentor keys. Set MENTOR_KEYS_JSON or create bots/supportive_peers/keys.json "
        "(gitignored JSON object of handle to key)."
    )


def mentor_handles(keys):
    if keys is None:
        return list(MENTORS)
    return [handle for handle in MENTORS if keys.get(handle)]


def is_career_card(post):
    if not isinstance(post, dict):
        return False
    if post.get("parent_id"):
        return False
    if post.get("ad_id"):
        return False
    if post.get("post_type") != "career_card":
        return False
    card = post.get("career_card") or {}
    if not one_line(card.get("title")):
        return False
    bot = post.get("bot") or {}
    if bot.get("handle") == "sponsored":
        return False
    return True


def replied_ids(state, handle):
    found = set()
    for value in (state.get("replied") or {}).get(handle) or []:
        try:
            found.add(int(value))
        except (TypeError, ValueError):
            continue
    return found


def author_handle(post):
    """Bot handle, or the human author when the card was not posted by a bot.

    Empty when both are missing. Does not add a reply cap; select_cards still
    stops at REPLY_CAP (and any hourly cap wrapped around plan_mentor).
    """
    bot = (post.get("bot") or {}).get("handle") or ""
    if bot:
        return bot
    human = post.get("human") or {}
    return human.get("handle") or human.get("username") or ""


def already_replied(post, handle, state_ids):
    try:
        post_id = int(post.get("id"))
    except (TypeError, ValueError):
        return True
    if post_id in state_ids:
        return True
    for reply in post.get("replies") or []:
        if not isinstance(reply, dict):
            continue
        if author_handle(reply) == handle:
            return True
    return False


def select_cards(cards, handle, state, limit=REPLY_CAP):
    """Recent unanswered Career Cards for one mentor, in feed order, capped."""
    state_ids = replied_ids(state, handle)
    chosen = []
    seen = set()
    for post in cards or []:
        if not is_career_card(post):
            continue
        try:
            post_id = int(post.get("id"))
        except (TypeError, ValueError):
            continue
        if post_id in seen:
            continue
        seen.add(post_id)
        if author_handle(post) == handle:
            continue
        if already_replied(post, handle, state_ids):
            continue
        chosen.append(post)
        if len(chosen) >= limit:
            break
    return chosen


def mode_for(handle, post_id):
    sequence = MODES[handle]
    try:
        number = int(post_id)
    except (TypeError, ValueError):
        number = 0
    return sequence[number % len(sequence)]


def card_blob(post):
    card = post.get("career_card") or {}
    parts = [
        card.get("title"),
        card.get("what_it_does"),
        card.get("who_hires"),
        card.get("skills_needed"),
        card.get("why_now"),
    ]
    return " ".join(one_line(part) for part in parts if part).casefold()


def topic_for(post):
    blob = card_blob(post)
    for topic in TOPICS:
        if any(key in blob for key in topic.keys):
            return topic
    return DEFAULT_TOPIC


def clip_field(text, fallback, limit):
    text = snip(text or fallback, limit)
    if text.endswith("…"):
        return text
    return text.rstrip(" .;:")


def card_fields(post):
    card = post.get("career_card") or {}
    return (
        clip_field(card.get("title"), "this Career Card", SNIP_TITLE),
        clip_field(card.get("what_it_does"), "the work on the card", SNIP_WHAT),
        clip_field(card.get("who_hires"), "unspecified", SNIP_WHO),
        clip_field(card.get("skills_needed"), "the skills on the card", SNIP_SKILLS),
        clip_field(card.get("why_now"), "the card's why-now", SNIP_WHO),
    )


def _bls_beside(topic):
    return (
        f"BLS counts {topic.adjacent} beside this pitch, not as this title. "
        f"{topic.adjacent_url}"
    )


def render_reply(handle, mode, post):
    """Scripted reply. One citation, one epistemic label, on the card's own terms."""
    topic = topic_for(post)
    title, what, who, skills, _why = card_fields(post)
    if handle == "forge_mentor":
        text = _forge(mode, title, what, who, skills, topic)
    elif handle == "blueprint_bea":
        text = _bea(mode, title, what, who, skills, topic)
    elif handle == "ledger_lin":
        text = _lin(mode, title, what, who, skills, topic)
    elif handle == "mentor_mae":
        text = _mae(mode, title, what, who, skills, topic)
    else:
        raise KeyError(handle)
    return fit(text)


def _forge(mode, title, what, who, skills, topic):
    if mode == "steelman":
        return (
            f"[Speculative] Steelman of {title}. Tasks on the card: {what}. "
            f"Acemoglu and Restrepo describe new tasks that reinstate labor after automation displaces it. "
            f"That is a demand test, not a job offer. {ACEMOGLU_URL}"
        )
    if mode == "critique":
        return (
            f"[Established] Steelman of {title}: {what}. "
            f'Counter: "{who}" names no payer or year for those tasks. {_bls_beside(topic)}'
        )
    if mode == "rival":
        return (
            f"[Established] Rival to {title}: give the tasks to {topic.rival} instead of a new title. "
            f"The card says {what}. {topic.rival_url}"
        )
    return (
        f"[Established] For {title}, practice the tasks in {skills}, "
        f"then set them next to {topic.adjacent}, which BLS counts. {topic.adjacent_url}"
    )


def _bea(mode, title, what, who, skills, topic):
    if mode == "steelman":
        return (
            f"[Speculative] Label {title} speculative. Steelman: the work is {what}. "
            f"Autor et al. find that the majority of current employment is in job specialties introduced since 1940. "
            f"That is not a vacancy for this title. {AUTOR_URL}"
        )
    if mode == "critique":
        return (
            f"[Speculative] Steelman of {title}: {what}. "
            f'Counter: it still needs a label and a year. "{who}" is a sector. {_bls_beside(topic)}'
        )
    if mode == "rival":
        return (
            f"[Established] A labeled rival to {title} is {topic.rival}, already in the handbook. "
            f"Keep the new title speculative, not a promise. The card says {what}. {topic.rival_url}"
        )
    return (
        f"[Established] To label {title} as practice, not a posting, build {skills}. "
        f"Handbook neighbor BLS counts: {topic.adjacent}. {topic.adjacent_url}"
    )


def _lin(mode, title, what, who, skills, topic):
    if mode == "steelman" and topic.exposure:
        return (
            f"[Emerging research] Evidence on {title}: {what}. "
            f"Eloundou et al. measure task exposure to LLM software and do not predict an adoption timeline. "
            f"Exposure is not a hire. {ELOUNDOU_URL}"
        )
    if mode == "steelman" and topic.timeline:
        return (
            f"[Emerging research] Evidence on {title}: {what}. "
            f"Preskill writes that the 100-qubit NISQ computer will not change the world right away, "
            f"so this is not a hiring clock. {PRESKILL_URL}"
        )
    if mode == "steelman":
        return (
            f"[Speculative] Evidence on {title}: {what}. "
            f"Autor et al. find that the majority of current employment is in job specialties introduced since 1940. "
            f"That finding is not an opening for this title. {AUTOR_URL}"
        )
    if mode == "critique" and topic.timeline:
        return (
            f"[Emerging research] Evidence on {title}: steelman is {what}. "
            f'Counter: "{who}" is not a clock. '
            f"Preskill writes that the 100-qubit NISQ computer will not change the world right away. {PRESKILL_URL}"
        )
    if mode == "critique" and topic.exposure:
        return (
            f"[Emerging research] Evidence on {title}: steelman is {what}. "
            f'Counter: "{who}" is not a series. '
            f"Eloundou et al. measure task exposure to LLM software and do not predict an adoption timeline. {ELOUNDOU_URL}"
        )
    if mode == "critique":
        return (
            f"[Established] Evidence on {title}: steelman is {what}. "
            f'Counter: "{who}" has no series and no year. {_bls_beside(topic)}'
        )
    if mode == "rival":
        return (
            f"[Established] Evidence against a new headcount for {title}: BLS already counts {topic.rival}. "
            f"The card says {what}. {topic.rival_url}"
        )
    return (
        f"[Established] Evidence-first practice for {title}: {skills}. "
        f"Compare it with {topic.adjacent}. BLS counts that occupation. {topic.adjacent_url}"
    )


def _mae(mode, title, what, who, skills, topic):
    if mode == "steelman":
        return (
            f"[Speculative] An honest reading of {title}: {what}. "
            f"Autor et al. find that the majority of current employment is in job specialties introduced since 1940. "
            f"This card is still not an offer. {AUTOR_URL}"
        )
    if mode == "critique":
        return (
            f"[Established] Honest gap on {title}. Steelman: {what}. "
            f'Counter: "{who}" is hope, not a requisition. {_bls_beside(topic)}'
        )
    if mode == "rival":
        return (
            f"[Established] An honest rival to {title} is {topic.rival}, work people are already hired for. "
            f"Keep the new idea speculative. The card says {what}. {topic.rival_url}"
        )
    return (
        f"[Established] Honest next step for {title}: practice {skills}. "
        f"That builds skill, not a promise of the title. "
        f"Nearby hired work BLS counts: {topic.adjacent}. {topic.adjacent_url}"
    )


def known_titles(cards):
    found = set()
    for post in cards or []:
        if not isinstance(post, dict):
            continue
        title = one_line((post.get("career_card") or {}).get("title"))
        if title:
            found.add(title.casefold())
    return found


def posted_titles(state, handle):
    found = set()
    for title in (state.get("posted_titles") or {}).get(handle) or []:
        text = one_line(title)
        if text:
            found.add(text.casefold())
    return found


def pick_card_post(handle, cards, state):
    taken = known_titles(cards) | posted_titles(state, handle)
    for card in CARD_POSTS[handle]:
        if card["title"].casefold() in taken:
            continue
        return Action(kind="card", title=card["title"], card=dict(card), citation=_trailing_url(card["why_now"]))
    return None


def _trailing_url(text):
    tail = one_line(text).rpartition(" ")[2]
    return tail if tail.startswith("http") else ""


def plan_mentor(handle, cards, state):
    """At most REPLY_CAP replies, plus at most one Career Card when few cards remain."""
    targets = select_cards(cards, handle, state, limit=REPLY_CAP)
    actions = []
    for post in targets:
        mode = mode_for(handle, post.get("id"))
        content = render_reply(handle, mode, post)
        title = one_line((post.get("career_card") or {}).get("title"))
        actions.append(
            Action(
                kind="reply",
                title=title,
                content=content,
                post_id=int(post["id"]),
                mode=mode,
                citation=_trailing_url(content),
            )
        )
    if len(targets) < FEW_NEW_CARDS:
        card = pick_card_post(handle, cards, state)
        if card is not None and len([a for a in actions if a.kind == "card"]) < POST_CAP:
            actions.append(card)
    return actions


def format_actions(handle, actions):
    if not actions:
        return f"{handle}: nothing to do"
    lines = [f"{handle}: {len(actions)} planned"]
    for action in actions:
        if action.kind == "reply":
            lines.append(f"  reply post_id={action.post_id} mode={action.mode} title={action.title}")
            lines.append(f"    {action.content}")
        else:
            lines.append(f"  post career_card title={action.title}")
            for key in ("what_it_does", "who_hires", "skills_needed", "why_now"):
                lines.append(f"    {key}: {action.card[key]}")
    return "\n".join(lines)


def warn(message, err):
    print(message, file=err)


def publish(api, handle, key, actions, state, state_path, err):
    """Post actions. On 401, skip the rest of this mentor. Never print the key."""
    for index, action in enumerate(actions):
        if index and api.pause:
            api.sleep(api.pause)
        if action.kind == "reply":
            body = {"content": action.content, "parent_id": action.post_id}
            path = "/api/posts"
        else:
            body = action.card
            path = "/api/career-cards"
        try:
            status, _payload = api.request("POST", path, key=key, body=body)
        except Exception:
            warn(f"warning: {handle} request failed", err)
            return "error"
        if status == 401:
            warn(f"warning: skipping {handle}: API key rejected (401), key={mask_key(key)}", err)
            return "unauthorized"
        if status not in (200, 201):
            warn(f"warning: {handle} {action.kind} failed with status {status}", err)
            continue
        if action.kind == "reply":
            bucket = state.setdefault("replied", {}).setdefault(handle, [])
            if action.post_id not in bucket:
                bucket.append(action.post_id)
        else:
            bucket = state.setdefault("posted_titles", {}).setdefault(handle, [])
            if action.title not in bucket:
                bucket.append(action.title)
        if state_path is not None:
            save_state(state_path, state)
    return "ok"


def fetch_cards(api):
    # /api/career-cards is the feed alias that returns each card with its replies.
    # Search hits do not include reply authors, so they cannot answer "already replied?".
    status, payload = api.request("GET", "/api/career-cards", params={"limit": CARD_LIMIT})
    if status != 200 or not isinstance(payload, list):
        raise RuntimeError(f"GET /api/career-cards returned status {status}")
    return payload


def run_once(api, keys, state, *, dry_run=False, state_path=None, cards=None, out=None, err=None):
    out = print if out is None else out
    err = sys.stderr if err is None else err
    if cards is None:
        cards = fetch_cards(api)
    reports = []
    for handle in mentor_handles(keys):
        actions = plan_mentor(handle, cards, state)
        out(format_actions(handle, actions))
        reports.append((handle, actions))
        if dry_run:
            continue
        key = None if keys is None else keys.get(handle)
        if not key:
            warn(f"warning: skipping {handle}: no API key", err)
            continue
        publish(api, handle, key, actions, state, state_path, err)
    return reports


def jittered_interval(interval, rng):
    """interval ± 10%, and at least one second."""
    interval = float(interval)
    if interval <= 0:
        raise ValueError("interval must be positive")
    span = interval * 0.10
    return max(1.0, interval + rng.uniform(-span, span))


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Post and reply as the MindGuild mentor bots.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="run one pass and exit")
    group.add_argument("--loop", action="store_true", help="repeat forever")
    parser.add_argument("--interval", type=float, default=3600, help="seconds between loop passes (default 3600)")
    parser.add_argument("--dry-run", action="store_true", help="print planned replies and cards without posting")
    args = parser.parse_args(argv)
    if args.loop and args.interval <= 0:
        parser.error("--interval must be positive")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    base = (os.environ.get("SYNTHETICA_BASE") or DEFAULT_BASE).strip() or DEFAULT_BASE
    keys = resolve_keys(os.environ.get("MENTOR_KEYS_JSON"), KEYS_PATH, args.dry_run)
    state = load_state(STATE_PATH)
    api = Api(base)
    mode = "dry-run" if args.dry_run else "live"
    print(f"{mode} base={base.rstrip('/')}")
    if args.loop:
        rng = random.Random()
        while True:
            run_once(api, keys, state, dry_run=args.dry_run, state_path=None if args.dry_run else STATE_PATH)
            delay = jittered_interval(args.interval, rng)
            print(f"sleep {delay:.0f}s")
            time.sleep(delay)
    else:
        run_once(api, keys, state, dry_run=args.dry_run, state_path=None if args.dry_run else STATE_PATH)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
