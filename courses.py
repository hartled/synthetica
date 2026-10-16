"""Course suggestions for Career Card skills.

Each entry maps keyword patterns found in a card's free-text skills_needed to
one Coursera page. Every URL below was fetched on 2026-10-16 and returned the
named course (og:title shown in the comment). Links are plain and untracked.

Tracked (affiliate) links can be swapped in later without a code change:
set COURSE_LINKS_JSON to a JSON object of {course_id: url}. Only https URLs
whose host is exactly one of COURSE_LINK_HOSTS (comma-separated) are accepted.
The default hosts are www.coursera.org plus two confirmed Impact tracking
hosts: coursera.pxf.io (the host Coursera's own pages call, campaign 14726)
and imp.i384100.net (the host on published Coursera affiliate links for that
same campaign). Userinfo, backslashes, and whitespace are rejected: Python's
urlsplit reads https://evil.com\\@www.coursera.org/ as Coursera, and browsers
navigate to evil.com. Anything else falls back to the plain URL.

Suggestions are learning resources, not job promises; Career Cards stay
speculative.
"""

import json
import os
import re
from urllib.parse import urlsplit

DISCLOSURE = ("Course links are learning suggestions, not job promises. "
              "MindGuild may later earn a commission from some course links.")

# id: (title, plain url, keyword regex)
COURSES = {
    "statistics": ("Introduction to Statistics (Stanford)", "https://www.coursera.org/learn/stanford-statistics",
                   r"\bstatistic"),
    "python": ("Programming for Everybody (Getting Started with Python)", "https://www.coursera.org/learn/python",
               r"\bpython\b|\bcoding\b|\bprogramming\b|\bsoftware development\b"),
    "ml": ("Machine Learning Specialization", "https://www.coursera.org/specializations/machine-learning-introduction",
           r"\bmachine learning\b|\bneural networks?\b|\bdeep learning\b"),
    "data": ("Google Data Analytics", "https://www.coursera.org/professional-certificates/google-data-analytics",
             r"\bdata[- ](?:analysis|analytics|literacy|science)\b|\bspreadsheets?\b|\bsql\b"),
    "security": ("Google Cybersecurity", "https://www.coursera.org/professional-certificates/google-cybersecurity",
                 r"\bsecurity\b|\bthreat\b|\bcyber"),
    "it": ("Google IT Support", "https://www.coursera.org/professional-certificates/google-it-support",
           r"\bit experience\b|\bit support\b|\bnetwork(?:ing)?\b|\btroubleshoot"),
    "pm": ("Google Project Management", "https://www.coursera.org/professional-certificates/google-project-management",
           r"\bproject management\b|\bneeds analysis\b|\bstakeholder"),
    "ai": ("AI For Everyone", "https://www.coursera.org/learn/ai-for-everyone",
           r"\bai\b|\bartificial intelligence\b"),
    "prompt": ("Prompt Engineering for ChatGPT", "https://www.coursera.org/learn/prompt-engineering",
               r"\bprompt"),
    "quantum": ("Introduction to Quantum Information", "https://www.coursera.org/learn/introduction-to-quantum-information",
                r"\bquantum\b"),
}
_RX = {cid: re.compile(rx, re.I) for cid, (_t, _u, rx) in COURSES.items()}
DEFAULT_HOSTS = ("www.coursera.org", "coursera.pxf.io", "imp.i384100.net")
MAX_PER_CARD = 3


def _hosts():
    raw = os.environ.get("COURSE_LINK_HOSTS")
    if not raw:
        return set(DEFAULT_HOSTS)
    return {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}


def allowed_course_url(url, hosts):
    """True when url is https and its host is an exact allowlist member.

    Rejects the urlsplit/browser backslash mismatch, userinfo, and whitespace.
    """
    if not isinstance(url, str):
        return False
    raw = url.strip()
    if not raw or any(ord(ch) <= 32 or ch == "\\" for ch in raw):
        return False
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host not in hosts:
        return False
    authority = parts.netloc.lower()
    return authority == host or authority == host + ":443"


def _overrides():
    raw = os.environ.get("COURSE_LINKS_JSON") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    hosts, out = _hosts(), {}
    for cid, url in data.items():
        if cid not in COURSES or not isinstance(url, str):
            continue
        cleaned = url.strip()
        if allowed_course_url(cleaned, hosts):
            out[cid] = cleaned
    return out


def link_for(cid):
    return _overrides().get(cid) or COURSES[cid][1]


def match_courses(skills_text, limit=MAX_PER_CARD):
    """Courses whose keywords appear in skills_text, in COURSES order."""
    if not isinstance(skills_text, str) or not skills_text.strip():
        return []
    over = _overrides()
    out = []
    for cid, (title, url, _rx) in COURSES.items():
        if _RX[cid].search(skills_text):
            out.append({"id": cid, "title": title, "url": over.get(cid) or url, "provider": "Coursera"})
            if len(out) >= limit:
                break
    return out
