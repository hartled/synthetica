"""Course suggestions for Career Card skills.

Each entry maps keyword patterns found in a card's free-text skills_needed to
one Coursera page. Every URL below was fetched on 2026-10-16 and returned the
named course (og:title shown in the comment). Links are plain and untracked.

Tracked (affiliate) links can be swapped in later without a code change:
set COURSE_LINKS_JSON to a JSON object of {course_id: url}. Only https URLs
on COURSE_LINK_HOSTS (comma-separated; default www.coursera.org and the
Impact domains coursera.pxf.io / imp.i384100.net) are accepted. Anything else
falls back to the plain URL.

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
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


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
        parts = urlsplit(url.strip())
        if parts.scheme == "https" and (parts.hostname or "").lower() in hosts:
            out[cid] = url.strip()
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
