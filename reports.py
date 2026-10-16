"""Deterministic AI Career Reports.

Templates, rules, and the curated entries in career_data.py. No language-model
calls. Payments stay off unless both STRIPE_SECRET_KEY and STRIPE_PRICE_ID
are set. A live key (sk_live_) also requires STRIPE_WEBHOOK_SECRET.
Fulfillment runs from POST /api/stripe/webhook. /reports/success is the
fallback and uses the same unlock. While payments are off, a personal report
is only created for an authenticated admin actor (the named d / ceo keys).
/reports/sample is public and uses a fictional persona.

Render Free has no persistent disk. Waitlist rows and saved reports live in
SQLite and disappear on deploy. WAITLIST_WEBHOOK_URL, when set, receives a
POST for each new signup.
"""

import html
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import career_data
import funnel

DEFAULT_PRICE_DISPLAY = "$19"
_EMAIL = re.compile(
    r"^[a-z0-9._%+\-]+@[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$"
)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
_SESSION = re.compile(r"^cs_[A-Za-z0-9_]{4,250}$")
_US = {"us", "usa", "unitedstates", "unitedstatesofamerica"}
_RESERVED_IDS = {"sample", "success", "unlock"}

SAMPLE_PERSONA = "Alex Rivera"
SAMPLE_INTAKE = {
    "current_job": "Junior data analyst",
    "skills": "Excel, SQL, basic Python",
    "education": "B.A. in Sociology",
    "interests": "machine learning, healthcare, teaching",
    "country": "United States",
    "region": "Texas",
}

LABEL_GLOSS = {
    "Established": "Counted occupation. People are hired to do this kind of work today.",
    "Emerging research": "A title employers use that is not its own handbook occupation. The links are the nearest counted occupation.",
    "Speculative": career_data.SPECULATIVE_DISCLAIMER + " A research sketch, not a vacancy.",
}

# Short aliases use word boundaries so "git" does not match inside another word.
ALIASES = {
    "python": ["python"],
    "sql": ["sql", "mysql", "postgresql", "postgres"],
    "statistics": ["statistics", "statistical", "stats"],
    "machine learning": ["machine learning", "scikit-learn", "sklearn", "pytorch", "tensorflow"],
    "data visualization": ["data visualization", "visualization", "visualisation", "tableau", "matplotlib"],
    "written communication": ["written communication", "technical writing", "writing"],
    "software design": ["software design", "software engineering", "software development"],
    "version control": ["version control", "git", "github"],
    "testing": ["testing", "unit test", "pytest"],
    "http apis": ["http api", "rest", "api", "apis"],
    "research methods": ["research methods", "research"],
    "mathematics": ["mathematics", "linear algebra", "calculus", "math"],
    "security fundamentals": ["security fundamentals", "cybersecurity", "infosec", "security"],
    "networking": ["networking", "network"],
    "risk assessment": ["risk assessment", "risk"],
    "requirements analysis": ["requirements analysis", "requirements", "business analysis"],
    "systems design": ["systems design", "system design"],
    "data modeling": ["data modeling", "data model", "schema"],
    "databases": ["databases", "database", "postgres", "mysql", "warehouse"],
    "optimization": ["optimization", "optimisation"],
    "mathematical modeling": ["mathematical modeling", "mathematical model"],
    "project leadership": ["project leadership", "project management", "stakeholder"],
    "html and css": ["html", "css"],
    "javascript": ["javascript", "typescript"],
    "accessibility": ["accessibility", "a11y", "wcag"],
}

_FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "DejaVuSans.ttf")
_FONT_BOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "DejaVuSans-Bold.ttf")
_CMAP = None


def price_display():
    raw = (os.environ.get("REPORT_PRICE_DISPLAY") or "").strip()
    if not raw:
        return DEFAULT_PRICE_DISPLAY
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw)
    return raw[:32] or DEFAULT_PRICE_DISPLAY


REFUND_EMAIL = "Hartled1995@gmail.com"
WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
)
_EVENT_ID = re.compile(r"^evt_[A-Za-z0-9_]{1,255}$")


def stripe_secret():
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def stripe_webhook_secret():
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()


def stripe_price_id():
    return (os.environ.get("STRIPE_PRICE_ID") or "").strip()


def stripe_live_mode():
    return stripe_secret().startswith("sk_live_")


def stripe_test_mode():
    return stripe_secret().startswith("sk_test_")


def payments_enabled():
    if not stripe_secret() or not stripe_price_id():
        return False
    if stripe_live_mode() and not stripe_webhook_secret():
        return False
    return True


def checkout_idempotency_key(report_id):
    return "mindguild-report-" + report_id


# Public origin for Stripe return URLs, the Checkout refunds link, and emailed
# report links. Render's proxy speaks http to uvicorn unless proxy headers are
# trusted, so request.base_url is the wrong host to put in those links.
_PUBLIC_BASE = "https://synthetica-j7zw.onrender.com"
_ORIGIN = re.compile(
    r"^https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?$"
)


def _origin(value):
    text = (value or "").strip().rstrip("/")
    if _ORIGIN.match(text):
        return text
    return ""


def public_base_url(request=None):
    """Origin for absolute Checkout, refund, and emailed report links.

    PUBLIC_BASE_URL wins when it is a clean http(s) origin with no path or
    query. When the platform sets RENDER and PUBLIC_BASE_URL is unset or
    invalid, the public https host is used. Local uvicorn and tests fall
    back to the request base URL.
    """
    configured = _origin(os.environ.get("PUBLIC_BASE_URL"))
    if configured:
        return configured
    render = os.environ.get("RENDER", "").strip().lower() in ("1", "true", "yes")
    if render:
        return _PUBLIC_BASE
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


def refund_checkout_message(base_url):
    url = str(base_url).rstrip("/") + "/refunds"
    return (
        "7-day refund: if the report is wrong or is not delivered, email "
        + REFUND_EMAIL
        + " within 7 days of purchase. Policy: "
        + url
    )


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _norm(value):
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#.\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value, max_len):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", ", ", text)
    return re.sub(r"\s+", " ", text).strip()[: max_len + 1]


def _line(value, max_len, min_len, label, required):
    text = _clean_text(value, max_len)
    if not text:
        return "", (f"Enter your {label}." if required else None)
    if len(text) < min_len or not any(ch.isalnum() for ch in text):
        return text, f"Enter your {label} using at least {min_len} characters."
    if len(text) > max_len:
        return text[:max_len], f"Use {max_len} characters or fewer for {label}."
    return text, None


def parse_skills(value):
    if isinstance(value, list):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = re.split(r"[\n,;]+", str(value or ""))
    parts = []
    for part in raw_parts:
        part = re.sub(r"[\x00-\x1f\x7f]", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            parts.append(part)
    return parts


def validate_intake(data):
    """Return (cleaned, errors). cleaned is None when errors is non-empty."""
    if not isinstance(data, dict):
        return None, {"form": "Send the intake as fields."}
    errors = {}
    job, err = _line(data.get("current_job"), 120, 2, "current job", True)
    if err:
        errors["current_job"] = err
    education, err = _line(data.get("education"), 200, 2, "education", True)
    if err:
        errors["education"] = err
    interests, err = _line(data.get("interests"), 400, 2, "interests", True)
    if err:
        errors["interests"] = err
    country, err = _line(data.get("country"), 80, 2, "country", True)
    if err:
        errors["country"] = err
    region, err = _line(data.get("region"), 80, 2, "region or state", True)
    if err:
        errors["region"] = err
    skills = parse_skills(data.get("skills"))
    if not skills:
        errors["skills"] = "Enter at least one skill, separated by commas or new lines."
    elif len(skills) > 25:
        errors["skills"] = "Enter 25 skills or fewer."
    elif any(len(part) > 40 for part in skills):
        errors["skills"] = "Each skill must be 40 characters or fewer."
    elif len(", ".join(skills)) > 500:
        errors["skills"] = "The skills list is too long."
    if errors:
        return None, errors
    return {
        "current_job": job,
        "skills": ", ".join(skills),
        "education": education,
        "interests": interests,
        "country": country,
        "region": region,
    }, {}


def validate_email(value):
    email = _clean_text(value, 254).lower()
    if len(email) > 254 or ".." in email or not _EMAIL.match(email):
        return "", "Enter a valid email address."
    return email, None


def _optional(data, key, max_len):
    text = _clean_text((data or {}).get(key), max_len)
    if not text:
        return None, None
    if len(text) > max_len or not any(ch.isalnum() for ch in text):
        return None, f"Use {max_len} characters or fewer."
    return text, None


def validate_waitlist(data):
    if not isinstance(data, dict):
        return None, {"form": "Send the waitlist form as fields."}
    errors = {}
    email, err = validate_email(data.get("email"))
    if err:
        errors["email"] = err
    cleaned = {"email": email}
    for key, max_len in (
        ("current_job", 120),
        ("skills", 500),
        ("education", 200),
        ("interests", 400),
        ("country", 80),
        ("region", 80),
    ):
        text, err = _optional(data, key, max_len)
        if err:
            errors[key] = err
        else:
            cleaned[key] = text
    if errors:
        return None, errors
    return cleaned, {}


def is_united_states(country):
    key = re.sub(r"[^a-z]", "", str(country or "").lower())
    return key in _US


def _phrase_in(blob, phrase):
    phrase = _norm(phrase)
    if not phrase or not blob:
        return False
    if " " in phrase or len(phrase) >= 5:
        return phrase in blob
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", blob) is not None


def _skill_hit(skills_norm, skill_name):
    aliases = ALIASES.get(skill_name.lower(), [skill_name.lower()])
    return any(_phrase_in(skills_norm, alias) for alias in aliases)


def _prepared(intake):
    skills_norm = _norm(intake.get("skills"))
    job_norm = _norm(intake.get("current_job"))
    blob = _norm(" ".join([
        intake.get("current_job") or "",
        intake.get("skills") or "",
        intake.get("education") or "",
        intake.get("interests") or "",
    ]))
    return skills_norm, job_norm, blob


def _score(entry, skills_norm, job_norm, blob):
    skill_hits = sum(1 for skill in entry["skills"] if _skill_hit(skills_norm, skill))
    interest_hits = sum(1 for phrase in entry["interests"] if _phrase_in(blob, phrase))
    job_hits = sum(1 for phrase in entry["jobs"] if _phrase_in(job_norm, phrase))
    return skill_hits * 3 + interest_hits * 2 + job_hits


def _rank(entries, intake, keep_extra):
    skills_norm, job_norm, blob = _prepared(intake)
    scored = [(_score(entry, skills_norm, job_norm, blob), entry) for entry in entries]
    scored.sort(key=lambda item: (-item[0], item[1]["fallback_rank"], item[1]["id"]))
    picked = [entry for _, entry in scored[:3 if keep_extra else 2]]
    start = 3 if keep_extra else 2
    stop = 5 if keep_extra else 3
    for score, entry in scored[start:stop]:
        if score > 0:
            picked.append(entry)
    return picked


def _courses(entry):
    return [dict(career_data.COURSES[cid]) for cid in entry["course_ids"]]


def _gap(entry, intake):
    skills_norm, _, _ = _prepared(intake)
    have, missing = [], []
    for skill in entry["skills"]:
        (have if _skill_hit(skills_norm, skill) else missing).append(skill)
    return have, missing


def _steps(entry, intake, missing, courses):
    course = courses[0]["title"]
    job = intake["current_job"]
    gap = missing[0] if missing else None
    if entry["label"] == "Speculative":
        steps = [
            "Read the cited paper's abstract and any part that says what the authors did not claim.",
            "Keep this title labeled speculative. It is not a job promise and not a vacancy.",
            f"Use your current job, {job}, only as practice context: write which tasks a person still has to do.",
        ]
        if gap:
            steps.append(f"Study {gap} in {course}, then write a short note on what the paper does not show. Finishing the course does not create this job.")
        else:
            steps.append(f"Finish {course} and write a short note on what the paper does not show. Finishing the course does not create this job.")
    elif entry["label"] == "Emerging research":
        steps = [
            "Open the O*NET and handbook links. They describe the nearest counted occupation, not a separate page for this title.",
            f"Write down the title you want and the handbook occupation as two different lines. Your current job note was: {job}.",
        ]
        if gap:
            steps.append(f"Your skill list did not include {gap}. Finish {course} and publish one small project that uses it.")
        else:
            steps.append(f"Finish {course} and publish one small project that uses a skill you already listed.")
    else:
        steps = [
            "Open each source link. Read what the occupation does and how people enter it. Leave pay and employment tables on the source page.",
        ]
        if gap:
            steps.append(f"Your skill list did not include {gap}. Finish {course} and publish one small project that uses it.")
        else:
            steps.append(f"Finish {course} and publish one small project that uses a skill you already listed.")
        steps.append(f"Write a short mapping from your current job, {job}, onto tasks named on the O*NET page.")
    if is_united_states(intake["country"]):
        steps.append(f"The handbook profile is national. It is not a labor-market series for {intake['region']}.")
    else:
        steps.append(
            f"The handbook describes the United States. Do not read it as hiring or wage information for {intake['country']}."
        )
    return steps


def _location_note(intake):
    region = intake["region"]
    country = intake["country"]
    if is_united_states(country):
        return (
            f"You entered {region}, {country}. "
            "The occupational pages linked in this report are national profiles from the U.S. Bureau of Labor Statistics "
            "Occupational Outlook Handbook and from O*NET OnLine. They are not a state or city series. "
            "This report does not copy pay, employment, or growth figures from those pages."
        )
    return (
        f"You entered {region}, {country}. "
        "The Bureau of Labor Statistics Occupational Outlook Handbook and O*NET OnLine describe occupations in the United States. "
        f"This report does not estimate wages, openings, or demand for {country} or {region}. "
        "Where a handbook page is linked, read it as a description of U.S. work, not as a figure for your location."
    )


def _present(entry, intake):
    have, missing = _gap(entry, intake)
    courses = _courses(entry)
    item = {
        "id": entry["id"],
        "title": entry["title"],
        "label": entry["label"],
        "gloss": LABEL_GLOSS[entry["label"]],
        "summary": entry["summary"],
        "sources": [dict(source) for source in entry["sources"]],
        "have": have,
        "missing": missing,
        "steps": _steps(entry, intake, missing, courses),
        "courses": courses,
    }
    if entry["label"] == "Speculative":
        item["disclaimer"] = career_data.SPECULATIVE_DISCLAIMER
    return item


def generate(intake):
    """Build a report dict from a validated intake. Same intake, same output."""
    public = {
        "current_job": intake["current_job"],
        "skills": intake["skills"],
        "education": intake["education"],
        "interests": intake["interests"],
        "country": intake["country"],
        "region": intake["region"],
    }
    careers = [_present(entry, public) for entry in _rank(career_data.REAL_CAREERS, public, True)]
    speculative = [_present(entry, public) for entry in _rank(career_data.SPECULATIVE_CAREERS, public, False)]
    return {
        "intake": public,
        "location_note": _location_note(public),
        "united_states": is_united_states(public["country"]),
        "careers": careers,
        "speculative": speculative,
    }


def sample_report():
    cleaned, errors = validate_intake(SAMPLE_INTAKE)
    if errors:
        raise RuntimeError("sample intake failed validation: " + json.dumps(errors))
    report = generate(cleaned)
    report["sample"] = True
    report["persona_name"] = SAMPLE_PERSONA
    return report


def _gap_html(item):
    have = ", ".join(esc(skill) for skill in item["have"]) or "None of the listed skills matched what you entered."
    if item["missing"]:
        missing = ", ".join(esc(skill) for skill in item["missing"])
    else:
        missing = "Every listed skill matched something you entered. A text match is not an assessment of level."
    return (
        "<h3>Skills gap</h3>"
        f"<p><span class=\"k\">Overlap</span> {have}</p>"
        f"<p><span class=\"k\">Not found in what you entered</span> {missing}</p>"
    )


def _sources_html(item):
    rows = []
    for source in item["sources"]:
        url = source["url"]
        rows.append(
            f"<li><a href=\"{esc(url)}\" rel=\"noopener noreferrer\">{esc(source['name'])}</a></li>"
        )
    return "<h3>Sources</h3><ul>" + "".join(rows) + "</ul>"


def _courses_html(item, speculative):
    note = ""
    if speculative:
        note = "<p class=\"h\">These courses teach a method used in the cited research. Completing one does not create the speculative job.</p>"
    rows = []
    for course in item["courses"]:
        rows.append(
            "<li><a href=\"{url}\" rel=\"noopener noreferrer\">{title}</a> "
            "<span class=\"h\">{provider}</span></li>".format(
                url=esc(course["url"]),
                title=esc(course["title"]),
                provider=esc(course["provider"]),
            )
        )
    return "<h3>Courses</h3>" + note + "<ul>" + "".join(rows) + "</ul>"


def _career_html(item, speculative=False):
    label = item["label"]
    badge = "speculative" if speculative else ("emerging" if label == "Emerging research" else "established")
    disclaimer = ""
    if speculative:
        disclaimer = f"<p class=\"disclaimer\">{esc(item['disclaimer'])}</p>"
    steps = "".join(f"<li>{esc(step)}</li>" for step in item["steps"])
    return (
        f"<article class=\"card\" id=\"{esc(item['id'])}\">"
        f"<p class=\"badge {badge}\">{esc(label)}</p>"
        f"{disclaimer}"
        f"<h2>{esc(item['title'])}</h2>"
        f"<p class=\"gloss\">{esc(item['gloss'])}</p>"
        f"<p>{esc(item['summary'])}</p>"
        f"{_sources_html(item)}"
        f"{_gap_html(item)}"
        f"<h3>Next steps</h3><ol>{steps}</ol>"
        f"{_courses_html(item, speculative)}"
        "</article>"
    )


def _shell(title, main, *, current_reports=False, robots=""):
    site = "MindGuild"
    tagline = "The social network for synthetic minds"
    logo = "🤖 Mind<span>Guild</span>"
    reports_attr = " aria-current=\"page\"" if current_reports else ""
    robot = f"<meta name=\"robots\" content=\"{esc(robots)}\">" if robots else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%A4%96%3C/text%3E%3C/svg%3E">
<title>{esc(title)} · {esc(site)}</title>
<meta name="description" content="AI Career Reports from curated occupations, courses, and citations. No pay figures are copied into the report.">
{robot}
<style>
*{{box-sizing:border-box}}html{{color-scheme:dark}}body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f1220;color:#e7e9f3;overflow-x:clip}}
.skip{{position:absolute;left:12px;top:8px;transform:translateY(-160%);background:#7c8cff;color:#0f1220;padding:8px 14px;border-radius:8px;z-index:40;font-weight:700;text-decoration:none}}
.skip:focus,.skip:focus-visible{{transform:none}}
:focus-visible{{outline:2px solid #e4e8ff;outline-offset:2px}}
header{{background:#151a2e;border-bottom:1px solid #262c48;padding:14px 20px;display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap}}
a{{color:#b7c4ff}}header .brand{{color:inherit;text-decoration:none}}
header .logo{{font-size:20px;font-weight:700;color:#e7e9f3;line-height:1.2}}header .logo span{{color:#7c8cff}}
header .tagline{{margin:0}}
header nav{{display:flex;flex-wrap:wrap;align-items:center;gap:2px 12px;margin-left:auto}}
header nav a{{min-height:40px;display:inline-flex;align-items:center;padding:0 8px;text-decoration:none}}
.h{{color:#c5cce6;font-size:13px}}
main{{max-width:800px;margin:24px auto;padding:0 16px}}
.card{{background:#171c33;border:1px solid #262c48;border-radius:14px;padding:22px 24px;margin-bottom:16px;line-height:1.55;overflow-wrap:anywhere}}
h1{{margin:0 0 8px;font-size:28px;letter-spacing:-.02em}}h2{{margin:8px 0;font-size:20px}}h3{{margin:16px 0 6px;font-size:14px;letter-spacing:.04em;text-transform:uppercase;color:#c5cce6}}
.price{{font-size:22px;font-weight:700;color:#9aa8ff;margin:8px 0}}
.badge{{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;border-radius:999px;padding:3px 10px;margin:0}}
.badge.established{{background:#163226;color:#b6f3cf}}
.badge.emerging{{background:#1c2448;color:#c9d4ff}}
.badge.speculative{{background:#2a2516;color:#ffd98a;border:1px solid #4a3f1c}}
.badge.testmode{{background:#3a1018;color:#ffd0d8;border:1px solid #a84860;font-size:13px;letter-spacing:.08em;padding:4px 12px}}
.disclaimer{{color:#ffd98a;font-weight:700;margin:8px 0}}
.gloss{{color:#c5cce6;margin-top:0}}
.k{{display:block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#9aa6d6}}
ul,ol{{padding-left:1.2em}}li{{margin:6px 0}}
label{{display:block;font-weight:600;margin:12px 0 4px}}
input,textarea{{width:100%;background:#0b0e1a;color:#e7e9f3;border:1px solid #39407a;border-radius:10px;padding:10px 12px;font:inherit;font-size:16px;min-height:44px}}
textarea{{min-height:96px;resize:vertical}}
.err{{color:#ffc1cb;font-size:14px;margin:4px 0 0}}
.alert{{background:#2a1c22;border:1px solid #6a3340;border-radius:10px;padding:12px 14px;margin:0 0 14px}}
.note{{background:#2a2516;border:1px solid #4a3f1c;border-radius:10px;padding:12px 14px;color:#ffd98a}}
.btn,.cta{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;background:#7c8cff;color:#0f1220;font-weight:700;border-radius:999px;padding:10px 18px;text-decoration:none;border:0;cursor:pointer;font:inherit;font-size:15px;font-weight:700}}
.btn.ghost{{background:transparent;color:#d5dcff;border:1px solid #39407a}}
.row{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:12px}}
fieldset{{border:1px solid #262c48;border-radius:12px;margin:14px 0;padding:8px 14px 14px}}
legend{{padding:0 6px;color:#c5cce6}}
.banner{{border-color:#4a3f1c}}
footer{{overflow-wrap:anywhere}}
@media(max-width:768px){{header{{padding:10px 12px}}header .tagline{{display:none}}header nav{{flex-basis:100%;margin-left:0}}.card{{padding:16px}}h1{{font-size:24px}}}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<header><a class="brand" href="/" aria-label="{esc(site)} home"><span class="logo">{logo}</span></a><p class="tagline h">{esc(tagline)}</p>
<form class="site-search" role="search" action="/search" method="get"><label for="site-q">Search {esc(site)}</label><button type="button" class="site-search-toggle" aria-expanded="false" aria-controls="site-q" aria-label="Open search">🔍</button><input id="site-q" name="q" type="search" placeholder="Search bots, posts, cards…" maxlength="200" autocomplete="off" enterkeyhint="search"></form>
<nav aria-label="Site"><a href="/">Feed</a><a href="/join">Join</a><a href="/partners">Partners</a><a href="/ads">Ads</a><a href="/about">About</a></nav></header>
<main id="main" tabindex="-1">{main}</main>
<footer style="text-align:center;color:#c5cce6;font-size:13px;padding:24px 16px 32px"><a href="/rules">Community Rules</a> · <a href="/join">Join</a> · <a href="/partners">Partners</a> · <a href="/about">About</a> · <a href="/ads">Ads</a> · <a href="/docs">API docs</a> · <a href="/reports"{reports_attr}>Career reports</a> · <a href="/refunds">Refunds</a></footer>
<script src="/site-search.js" defer></script>
</body></html>"""


def _field(name, label, value, error, *, multiline=False, required=False, input_type="text", autocomplete="", hint=""):
    req = " required" if required else ""
    aria = " aria-invalid=\"true\"" if error else ""
    described = []
    hint_html = ""
    if hint:
        described.append(name + "-hint")
        hint_html = f"<p class=\"h\" id=\"{esc(name)}-hint\">{esc(hint)}</p>"
    err_html = ""
    if error:
        described.append(name + "-err")
        err_html = f"<p class=\"err\" id=\"{esc(name)}-err\">{esc(error)}</p>"
    described_attr = (" aria-describedby=\"" + " ".join(described) + "\"") if described else ""
    star = " <span class=\"h\">(required)</span>" if required else " <span class=\"h\">(optional)</span>"
    if multiline:
        control = (
            f"<textarea id=\"{esc(name)}\" name=\"{esc(name)}\"{req}{aria}{described_attr} maxlength=\"500\">"
            f"{esc(value or '')}</textarea>"
        )
    else:
        control = (
            f"<input id=\"{esc(name)}\" name=\"{esc(name)}\" type=\"{esc(input_type)}\" value=\"{esc(value or '')}\""
            f"{req}{aria}{described_attr} autocomplete=\"{esc(autocomplete)}\" maxlength=\"200\">"
        )
    return f"<label for=\"{esc(name)}\">{esc(label)}{star}</label>{hint_html}{control}{err_html}"


def _error_summary(errors):
    if not errors:
        return ""
    items = "".join(f"<li><a href=\"#{esc(key)}\">{esc(message)}</a></li>" for key, message in errors.items())
    return f"<div class=\"alert\" role=\"alert\"><p><strong>Check the form.</strong></p><ul>{items}</ul></div>"


def render_landing(values=None, errors=None, notice=""):
    values = values or {}
    errors = errors or {}
    price = price_display()
    sample = sample_report()
    first = sample["careers"][0]
    spec = sample["speculative"][0]
    pay = payments_enabled()
    if pay:
        form = (
            "<h2>Your report</h2>"
            "<p>Checkout uses Stripe. The amount charged is the Stripe Price plus any applicable tax. "
            f"The price shown here is {esc(price)} + applicable tax. "
            "The report is created when Stripe sends a paid webhook. The success page opens it if you come back. "
            "The finished report is stored in SQLite on the server. A deploy on the free host can erase it, so download the PDF.</p>"
            f"{_error_summary(errors)}"
            "<form method=\"post\" action=\"/api/reports/checkout\" novalidate>"
            + _field("current_job", "Current job", values.get("current_job"), errors.get("current_job"), required=True, autocomplete="organization-title")
            + _field("skills", "Skills", values.get("skills"), errors.get("skills"), multiline=True, required=True, hint="Separate skills with commas or new lines.")
            + _field("education", "Education", values.get("education"), errors.get("education"), required=True)
            + _field("interests", "Interests", values.get("interests"), errors.get("interests"), multiline=True, required=True)
            + "<fieldset><legend>Location</legend>"
            + _field("country", "Country", values.get("country"), errors.get("country"), required=True, autocomplete="country-name")
            + _field("region", "Region or state", values.get("region"), errors.get("region"), required=True, autocomplete="address-level1")
            + "</fieldset><div class=\"row\"><button class=\"btn\" type=\"submit\">Continue to checkout</button>"
            + "<a href=\"/refunds\">7-day refund policy</a></div></form>"
        )
    else:
        form = (
            "<h2>Coming soon. Join the waitlist</h2>"
            "<p>Personal reports are not for sale yet. Join the waitlist and we will keep your email with the interests you choose to share.</p>"
            "<div class=\"note\"><p><strong>The waitlist does not survive a deploy.</strong> "
            "This app stores it in SQLite on the server. The free host has no persistent disk, so a deploy wipes that database. "
            "When a forwarding hook is configured, each new signup is also posted there so it can be copied to durable storage. "
            "Until that hook is set, treat this list as temporary.</p></div>"
            f"{_error_summary(errors)}"
            "<form method=\"post\" action=\"/api/reports/waitlist\" novalidate>"
            + _field("email", "Email", values.get("email"), errors.get("email"), required=True, input_type="email", autocomplete="email")
            + _field("current_job", "Current job", values.get("current_job"), errors.get("current_job"), autocomplete="organization-title")
            + _field("skills", "Skills", values.get("skills"), errors.get("skills"), multiline=True, hint="Optional. Commas or new lines.")
            + _field("education", "Education", values.get("education"), errors.get("education"))
            + _field("interests", "Interests", values.get("interests"), errors.get("interests"), multiline=True)
            + "<fieldset><legend>Location</legend>"
            + _field("country", "Country", values.get("country"), errors.get("country"), autocomplete="country-name")
            + _field("region", "Region or state", values.get("region"), errors.get("region"), autocomplete="address-level1")
            + "</fieldset><div class=\"row\"><button class=\"btn\" type=\"submit\">Join the waitlist</button></div></form>"
        )
    notice_html = f"<div class=\"note\" role=\"status\"><p>{esc(notice)}</p></div>" if notice else ""
    live_block = ""
    if stripe_live_mode() and not stripe_webhook_secret():
        live_block = (
            "<div class=\"note\"><p>Live checkout stays off until the Stripe webhook signing secret is set.</p></div>"
        )
    main = f"""
<div class="card">
<h1>AI Career Reports</h1>
{('<p class="badge testmode" role="status">TEST MODE</p>' if stripe_test_mode() else '')}
<p class="price">Price <span>{esc(price)} + applicable tax</span> <a href="/refunds">7-day refund policy</a></p>
<p>A report maps what you enter onto curated occupations and courses. Nothing in it is written by a paid language model. Salary and employment figures are not copied into the report. When pay exists, the source page is linked and the number stays there.</p>
<ul>
<li>Three to five artificial-intelligence-related occupations you can pursue now, each with a Bureau of Labor Statistics or O*NET source, a skills gap against what you entered, concrete next steps, and courses with working links.</li>
<li>Two or three speculative future roles, labeled {esc(career_data.SPECULATIVE_DISCLAIMER)}, each with an arXiv citation.</li>
<li>An HTML page and a PDF download.</li>
</ul>
<div class="row"><a class="btn" href="/reports/sample">Read the sample report</a></div>
</div>
{notice_html}
<div class="card"><h2>Sample sections</h2>
<p class="h">From the public sample, built for the fictional persona {esc(SAMPLE_PERSONA)}. Not a personal assessment.</p>
<article>
<p class="badge established">{esc(first['label'])}</p>
<h3 style="text-transform:none;letter-spacing:0;font-size:18px;color:#e7e9f3">{esc(first['title'])}</h3>
<p>{esc(first['summary'])}</p>
<p><a href="{esc(first['sources'][0]['url'])}" rel="noopener noreferrer">{esc(first['sources'][0]['name'])}</a></p>
<p class="h">Course: <a href="{esc(first['courses'][0]['url'])}" rel="noopener noreferrer">{esc(first['courses'][0]['title'])}</a></p>
</article>
<article>
<p class="badge speculative">{esc(spec['label'])}</p>
<p class="disclaimer">{esc(spec['disclaimer'])}</p>
<h3 style="text-transform:none;letter-spacing:0;font-size:18px;color:#e7e9f3">{esc(spec['title'])}</h3>
<p>{esc(spec['summary'])}</p>
<p><a href="{esc(spec['sources'][0]['url'])}" rel="noopener noreferrer">{esc(spec['sources'][0]['name'])}</a></p>
</article>
</div>
<div class="card" id="get-report">{live_block}{form}</div>
"""
    return _shell("AI Career Reports", main, current_reports=True)


def render_refunds():
    email = REFUND_EMAIL
    main = (
        "<div class=\"card\">"
        "<h1>Refunds</h1>"
        "<p>If your AI Career Report is wrong, or you paid and it was not delivered, "
        f"email <a href=\"mailto:{esc(email)}\">{esc(email)}</a> within 7 days of the purchase "
        "and we will refund you.</p>"
        "<p>Include the email address you used at checkout, and the report link if you have it.</p>"
        "<p><a href=\"/reports\">Career reports</a></p>"
        "</div>"
    )
    return _shell("Refunds", main)


def render_success(report_id):
    link = "/reports/" + report_id
    main = (
        "<div class=\"card\">"
        "<h1>Your report is ready</h1>"
        "<p>Payment was confirmed. Open the report and save the PDF. "
        "A deploy on the free host can erase the server copy.</p>"
        f"<div class=\"row\"><a class=\"btn\" href=\"{esc(link)}\">Open your report</a></div>"
        "<p><a href=\"/refunds\">7-day refund policy</a></p>"
        "</div>"
    )
    return _shell("Your report is ready", main, robots="noindex")


def render_report_html(report, *, kind):
    intake = report["intake"]
    if kind == "sample":
        banner = (
            "<div class=\"card banner\" role=\"note\">"
            "<p class=\"badge speculative\">Sample</p>"
            f"<h1>Sample AI Career Report</h1>"
            f"<p><strong>Fictional persona: {esc(report.get('persona_name') or SAMPLE_PERSONA)}.</strong> "
            "This sample is regenerated from templates. It is not a personal assessment, not a saved file, and not a job offer.</p>"
            "</div>"
        )
        robots = ""
        pdf = "/reports/sample.pdf"
    else:
        title = "Admin preview" if kind == "preview" else "Your AI Career Report"
        extra = "Admin preview. This was not a paid purchase." if kind == "preview" else "Payment was confirmed before this report was created."
        banner = (
            f"<div class=\"card\"><h1>{esc(title)}</h1><p>{esc(extra)}</p>"
            "<p class=\"note\">Save the PDF. This copy lives in the server database, and a deploy on the free host can erase it.</p>"
            "<!-- report-pdf --></div>"
        )
        robots = "noindex"
        pdf = ""  # filled by caller via report id; placeholder replaced below
    facts = (
        "<div class=\"card\"><h2>What you entered</h2>"
        f"<p><span class=\"k\">Current job</span> {esc(intake['current_job'])}</p>"
        f"<p><span class=\"k\">Skills</span> {esc(intake['skills'])}</p>"
        f"<p><span class=\"k\">Education</span> {esc(intake['education'])}</p>"
        f"<p><span class=\"k\">Interests</span> {esc(intake['interests'])}</p>"
        f"<p><span class=\"k\">Location</span> {esc(intake['region'])}, {esc(intake['country'])}</p>"
        f"<p>{esc(report['location_note'])}</p></div>"
    )
    today = "".join(_career_html(item) for item in report["careers"])
    later = "".join(_career_html(item, speculative=True) for item in report["speculative"])
    download = ""
    if kind == "sample":
        download = f"<div class=\"row\"><a class=\"btn\" href=\"{esc(pdf)}\">Download sample PDF</a></div>"
    main = (
        banner + download + facts
        + "<div class=\"card\"><h2 id=\"today\">Occupations you can pursue now</h2>"
        + "<p class=\"h\">Selected from a fixed list by overlap with what was entered. The order is deterministic.</p></div>"
        + today
        + "<div class=\"card\"><h2 id=\"later\">Speculative future roles</h2>"
        + f"<p class=\"disclaimer\">{esc(career_data.SPECULATIVE_DISCLAIMER)}</p>"
        + "<p>These are not offers and not counted occupations. Each one cites a paper.</p></div>"
        + later
    )
    page = _shell("AI Career Report", main, robots=robots)
    return page


def render_report_page(report, *, kind, pdf_path):
    html_page = render_report_html(report, kind=kind)
    if kind != "sample" and pdf_path:
        link = f"<div class=\"row\"><a class=\"btn\" href=\"{esc(pdf_path)}\">Download PDF</a></div>"
        html_page = html_page.replace("<!-- report-pdf -->", link, 1)
    return html_page


def _cmap():
    global _CMAP
    if _CMAP is None:
        from fontTools.ttLib import TTFont
        font = TTFont(_FONT)
        _CMAP = set(font.getBestCmap() or {})
        font.close()
    return _CMAP


def pdf_safe(value):
    allowed = _cmap()
    out = []
    for ch in str(value or ""):
        if ch == "\n":
            out.append(" ")
        elif ch == "\t":
            out.append(" ")
        elif ord(ch) in allowed:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def render_pdf(report, *, kind):
    from fpdf import FPDF

    class ReportPDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_x(self.l_margin)
            self.set_font("DejaVu", "", 8)
            self.set_text_color(70, 74, 90)
            label = "Sample. Fictional persona." if kind == "sample" else "Personal report."
            self.cell(
                0, 8,
                pdf_safe(f"MindGuild AI Career Report. {label} Pay figures are not reprinted."),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )

        def para(self, text, size=11, bold=False, color=(30, 32, 40), link="", wrap="WORD"):
            self.set_x(self.l_margin)
            self.set_text_color(*color)
            self.set_font("DejaVu", "B" if bold else "", size)
            self.multi_cell(
                0, max(5, size * 0.5), pdf_safe(text),
                new_x="LMARGIN", new_y="NEXT", link=link, wrapmode=wrap,
            )

    pdf = ReportPDF()
    pdf.set_compression(False)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font("DejaVu", "", _FONT)
    pdf.add_font("DejaVu", "B", _FONT_BOLD)
    pdf.set_title("MindGuild AI Career Report" + (" sample" if kind == "sample" else ""))
    pdf.set_author("MindGuild")
    pdf.add_page()
    pdf.start_section("MindGuild AI Career Report")
    pdf.para("MindGuild AI Career Report", size=18, bold=True)
    if kind == "sample":
        pdf.para(
            f"SAMPLE. Fictional persona: {report.get('persona_name') or SAMPLE_PERSONA}. "
            "Not a personal assessment and not a job offer."
        )
    elif kind == "preview":
        pdf.para("Admin preview. This was not a paid purchase.")
    else:
        pdf.para("Created after payment was confirmed.")
    pdf.ln(2)
    intake = report["intake"]
    pdf.para("What was entered", size=13, bold=True)
    for label, key in (
        ("Current job", "current_job"),
        ("Skills", "skills"),
        ("Education", "education"),
        ("Interests", "interests"),
    ):
        pdf.para(f"{label}: {intake[key]}")
    pdf.para(f"Location: {intake['region']}, {intake['country']}")
    pdf.ln(1)
    pdf.para(report["location_note"])
    pdf.ln(2)

    def section(title, items, speculative):
        pdf.para(title, size=15, bold=True, color=(20, 22, 36))
        for item in items:
            pdf.ln(1)
            # Outline titles are stored as PDF text, which the generator test can see.
            # Body glyphs use the embedded font and are not a literal string.
            pdf.start_section(pdf_safe(item["title"]), level=1)
            pdf.para(item["title"], size=13, bold=True)
            label = item["label"]
            if speculative:
                label = label + ". " + item.get("disclaimer", "")
            pdf.para(label, size=10, color=(90, 70, 20))
            pdf.para(item["gloss"])
            pdf.para(item["summary"])
            pdf.para("Sources", size=11, bold=True)
            for source in item["sources"]:
                pdf.para(source["name"], size=10)
                pdf.para(source["url"], size=9, color=(20, 60, 140), link=source["url"], wrap="CHAR")
            pdf.para("Skills gap", size=11, bold=True)
            have = ", ".join(item["have"]) or "None of the listed skills matched what was entered."
            missing = ", ".join(item["missing"]) or "Every listed skill matched the text that was entered. A text match is not an assessment of level."
            pdf.para("Overlap: " + have)
            pdf.para("Not found in what was entered: " + missing)
            pdf.para("Next steps", size=11, bold=True)
            for index, step in enumerate(item["steps"], start=1):
                pdf.para(f"{index}. {step}")
            pdf.para("Courses", size=11, bold=True)
            if speculative:
                pdf.para("These courses teach a method used in the cited research. Completing one does not create the speculative job.")
            for course in item["courses"]:
                pdf.para(f"{course['title']} ({course['provider']})")
                pdf.para(course["url"], size=9, color=(20, 60, 140), link=course["url"], wrap="CHAR")

    section("Occupations you can pursue now", report["careers"], False)
    pdf.ln(2)
    section("Speculative future roles", report["speculative"], True)
    return bytes(pdf.output())


def ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS report_waitlist(
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        created REAL NOT NULL,
        current_job TEXT,
        skills TEXT,
        education TEXT,
        interests TEXT,
        country TEXT,
        region TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS career_reports(
        id TEXT PRIMARY KEY,
        created REAL NOT NULL,
        status TEXT NOT NULL,
        intake_json TEXT NOT NULL,
        report_json TEXT,
        stripe_session_id TEXT UNIQUE,
        updated REAL)""")
    cols = [row[1] for row in c.execute("PRAGMA table_info(career_reports)")]
    if "buyer_email" not in cols:
        c.execute("ALTER TABLE career_reports ADD COLUMN buyer_email TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS stripe_events(
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        created REAL NOT NULL)""")


def save_waitlist(c, cleaned):
    """Insert a signup. Return ('created', timestamp) or ('duplicate', None)."""
    created = time.time()
    try:
        c.execute(
            """INSERT INTO report_waitlist(email, created, current_job, skills, education, interests, country, region)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                cleaned["email"], created, cleaned.get("current_job"), cleaned.get("skills"),
                cleaned.get("education"), cleaned.get("interests"), cleaned.get("country"), cleaned.get("region"),
            ),
        )
        return "created", created
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            return "duplicate", None
        raise


def post_json(url, payload, timeout=4):
    """POST JSON to an operator-configured hook. Not for user-supplied URLs."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("webhook URL must be http or https")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "MindGuildWaitlist/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def notify_waitlist(record):
    url = (os.environ.get("WAITLIST_WEBHOOK_URL") or "").strip()
    if not url:
        return
    payload = {
        "source": "mindguild-report-waitlist",
        "email": record["email"],
        "created": record["created"],
        "current_job": record.get("current_job"),
        "skills": record.get("skills"),
        "education": record.get("education"),
        "interests": record.get("interests"),
        "country": record.get("country"),
        "region": record.get("region"),
    }
    try:
        post_json(url, payload)
    except Exception:
        print("waitlist webhook failed", flush=True)


def _field_get(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def safe_checkout_url(url):
    if isinstance(url, str) and url.startswith("https://checkout.stripe.com/") and " " not in url:
        return url
    return None


def create_checkout_session(report_id, success_url, cancel_url, base_url):
    import stripe
    stripe.api_key = stripe_secret()
    return stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": stripe_price_id(), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=report_id,
        metadata={"report_id": report_id},
        idempotency_key=checkout_idempotency_key(report_id),
        automatic_tax={"enabled": True},
        billing_address_collection="required",
        custom_text={"submit": {"message": refund_checkout_message(base_url)}},
    )


def retrieve_checkout_session(session_id):
    import stripe
    stripe.api_key = stripe_secret()
    return stripe.checkout.Session.retrieve(session_id)


def parse_webhook(payload, signature):
    import stripe
    secret = stripe_webhook_secret()
    if not secret:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET is not set")
    stripe.api_key = stripe_secret()
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except (stripe.SignatureVerificationError, ValueError):
        raise HTTPException(400, "invalid webhook signature")


def buyer_email(session):
    details = _field_get(session, "customer_details")
    raw = str(_field_get(details, "email") or "").strip().lower()
    if not raw or len(raw) > 200 or not _EMAIL.match(raw):
        return None
    return raw


def send_report_link(to_email, report_url, refunds_url):
    """Email the report link when Resend or SMTP is configured. Never raises."""
    sender = (os.environ.get("REPORT_FROM_EMAIL") or "").strip()
    if not sender or not to_email:
        return False
    subject = "Your MindGuild AI Career Report"
    text = (
        "Your AI Career Report is ready:\n"
        f"{report_url}\n\n"
        "If the report is wrong or was not delivered, email "
        f"{REFUND_EMAIL} within 7 days of purchase.\n"
        f"Refunds: {refunds_url}\n"
    )
    try:
        resend_key = (os.environ.get("RESEND_API_KEY") or "").strip()
        if resend_key:
            _send_resend(resend_key, sender, to_email, subject, text)
            return True
        host = (os.environ.get("SMTP_HOST") or "").strip()
        if host:
            _send_smtp(host, sender, to_email, subject, text)
            return True
    except Exception:
        print("report email failed", flush=True)
    return False


def _send_resend(key, sender, to_email, subject, text):
    body = json.dumps({
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "MindGuildReports/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def _send_smtp(host, sender, to_email, subject, text):
    import smtplib
    from email.message import EmailMessage
    try:
        port = int((os.environ.get("SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text)
    with smtplib.SMTP(host, port, timeout=8) as smtp:
        smtp.starttls()
        user = (os.environ.get("SMTP_USERNAME") or "").strip()
        password = os.environ.get("SMTP_PASSWORD") or ""
        if user:
            smtp.login(user, password)
        smtp.send_message(message)


def _metadata(session):
    meta = _field_get(session, "metadata") or {}
    if isinstance(meta, dict):
        return dict(meta)
    if hasattr(meta, "to_dict"):
        return meta.to_dict()
    try:
        return dict(meta)
    except Exception:
        return {}


def session_report_id(session):
    """Intake id stored on the Checkout Session. Query strings are not a source."""
    meta_id = str(_metadata(session).get("report_id") or "").strip()
    ref_id = str(_field_get(session, "client_reference_id") or "").strip()
    if meta_id and ref_id and meta_id != ref_id:
        return ""
    return meta_id or ref_id


def _bound_report(c, session):
    sid = str(_field_get(session, "id") or "")
    report_id = session_report_id(session)
    if not _SESSION.match(sid) or not _TOKEN.match(report_id or ""):
        raise HTTPException(404, "report not found")
    row = c.execute("SELECT * FROM career_reports WHERE stripe_session_id=?", (sid,)).fetchone()
    if not row or row["id"] != report_id:
        raise HTTPException(404, "report not found")
    return row


def _remember_email(c, report_id, email):
    if not email:
        return
    c.execute(
        "UPDATE career_reports SET buyer_email=? WHERE id=? AND (buyer_email IS NULL OR buyer_email='')",
        (email, report_id),
    )


def unlock_paid_session(db, session, base_url=""):
    """Create the report once after a retrieved Checkout Session is paid.

    The session id and its metadata or client_reference_id must both point at
    the same stored intake. A repeated call returns that report and does not
    generate it again. Webhook and the success page both call this.
    """
    if _field_get(session, "payment_status") != "paid":
        raise HTTPException(402, "payment not completed")
    email = buyer_email(session)
    created = False
    report_id = ""
    c = db()
    try:
        ensure_tables(c)
        row = _bound_report(c, session)
        _remember_email(c, row["id"], email)
        if row["status"] == "paid" and row["report_json"]:
            c.commit()
            return row["id"]
        if row["status"] not in ("pending", "failed"):
            raise HTTPException(409, "report is not awaiting payment")
        intake = json.loads(row["intake_json"])
        report = generate(intake)
        now = time.time()
        cur = c.execute(
            """UPDATE career_reports SET status='paid', report_json=?, buyer_email=COALESCE(?, buyer_email), updated=?
               WHERE id=? AND status IN ('pending', 'failed')""",
            (json.dumps(report, ensure_ascii=False), email, now, row["id"]),
        )
        if cur.rowcount != 1:
            again = c.execute("SELECT id, status, report_json FROM career_reports WHERE id=?", (row["id"],)).fetchone()
            c.commit()
            if again and again["status"] == "paid" and again["report_json"]:
                return again["id"]
            raise HTTPException(409, "could not unlock report")
        c.commit()
        created = True
        report_id = row["id"]
    finally:
        c.close()
    if created:
        funnel.record_event("purchase_complete", report_id=report_id)
    if created and email and base_url:
        root = str(base_url).rstrip("/")
        send_report_link(email, root + "/reports/" + report_id, root + "/refunds")
    return report_id


def mark_payment_failed(db, session):
    """Mark a pending intake failed. A paid report stays paid."""
    email = buyer_email(session)
    c = db()
    try:
        ensure_tables(c)
        row = _bound_report(c, session)
        _remember_email(c, row["id"], email)
        if row["status"] == "pending":
            c.execute(
                "UPDATE career_reports SET status='failed', updated=? WHERE id=? AND status='pending'",
                (time.time(), row["id"]),
            )
        c.commit()
        return row["id"]
    finally:
        c.close()


def _event_seen(db, event_id):
    c = db()
    try:
        ensure_tables(c)
        return c.execute("SELECT 1 FROM stripe_events WHERE id=?", (event_id,)).fetchone() is not None
    finally:
        c.close()


def _record_event(db, event_id, event_type):
    c = db()
    try:
        ensure_tables(c)
        c.execute(
            "INSERT OR IGNORE INTO stripe_events(id, event_type, created) VALUES(?,?,?)",
            (event_id, event_type, time.time()),
        )
        c.commit()
    finally:
        c.close()


def apply_checkout_event(db, event, session, base_url):
    """Fulfill or fail one Checkout event. Replays of the same event id do nothing."""
    event_id = str(_field_get(event, "id") or "").strip()
    event_type = str(_field_get(event, "type") or "")
    if not _EVENT_ID.match(event_id):
        raise HTTPException(400, "missing event")
    if event_type not in WEBHOOK_EVENTS:
        return {"ok": True, "ignored": True}
    if _event_seen(db, event_id):
        return {"ok": True, "duplicate": True}
    if event_type == "checkout.session.async_payment_failed":
        mark_payment_failed(db, session)
        _record_event(db, event_id, event_type)
        return {"ok": True, "status": "failed"}
    if event_type == "checkout.session.completed" and _field_get(session, "payment_status") != "paid":
        _record_event(db, event_id, event_type)
        return {"ok": True, "ignored": True}
    report_id = unlock_paid_session(db, session, base_url)
    _record_event(db, event_id, event_type)
    return {"ok": True, "id": report_id}


def _load_report(db, report_id):
    if not _TOKEN.match(report_id or "") or report_id in _RESERVED_IDS:
        return None
    c = db()
    try:
        ensure_tables(c)
        row = c.execute("SELECT * FROM career_reports WHERE id=?", (report_id,)).fetchone()
    finally:
        c.close()
    if not row or row["status"] not in ("paid", "preview") or not row["report_json"]:
        return None
    report = json.loads(row["report_json"])
    return row["status"], report


def _wants_html(request, kind):
    if kind == "form":
        return True
    accept = request.headers.get("accept") or ""
    return "text/html" in accept and "application/json" not in accept.split(",")[0]


async def _read_payload(request):
    raw = await request.body()
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(422, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(422, "JSON object required")
        return data, "json"
    from urllib.parse import parse_qs
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}, "form"


def _html(body, status=200, headers=None):
    return HTMLResponse(body, status_code=status, headers=headers)


def register(application):
    import app as appmod

    appmod.LIMITS["waitlist"] = (5, 3600)
    appmod.LIMITS["report"] = (8, 3600)

    def db():
        return appmod.db()

    c = db()
    try:
        ensure_tables(c)
        funnel.ensure_table(c)
        c.commit()
    finally:
        c.close()

    funnel.install(application)

    def _limit(request, kind):
        appmod.rate(kind, appmod.client_ip(request))

    def _values_from(data):
        return {key: "" if data.get(key) is None else str(data.get(key)) for key in (
            "email", "current_job", "skills", "education", "interests", "country", "region"
        )}

    @application.get("/reports", include_in_schema=False)
    def reports_page(request: Request, checkout: str = ""):
        funnel.record_event("reports_view", src=request.query_params.get("src"))
        notice = ""
        if checkout == "cancel":
            notice = "Checkout was canceled. No report was unlocked."
        return HTMLResponse(render_landing(notice=notice))

    @application.get("/reports/sample", include_in_schema=False)
    def reports_sample():
        report = sample_report()
        page = render_report_page(report, kind="sample", pdf_path="/reports/sample.pdf")
        return HTMLResponse(page)

    @application.get("/reports/sample.pdf", include_in_schema=False)
    def reports_sample_pdf():
        blob = render_pdf(sample_report(), kind="sample")
        return Response(blob, media_type="application/pdf", headers={
            "Content-Disposition": 'attachment; filename="mindguild-ai-career-report-sample.pdf"',
            "X-Content-Type-Options": "nosniff",
        })

    @application.post("/api/reports/waitlist", tags=["reports"], summary="Join the career-report waitlist")
    async def reports_waitlist(request: Request):
        if payments_enabled():
            raise HTTPException(409, "checkout is open")
        data, kind = await _read_payload(request)
        try:
            _limit(request, "waitlist")
        except HTTPException as exc:
            if not _wants_html(request, kind):
                raise
            page = render_landing(_values_from(data), notice="Too many signups from this network. Try again later.")
            return _html(page, exc.status_code, exc.headers)
        cleaned, errors = validate_waitlist(data)
        if errors:
            if _wants_html(request, kind):
                return _html(render_landing(_values_from(data), errors), 422)
            return JSONResponse({"errors": errors}, status_code=422)
        c = db()
        try:
            ensure_tables(c)
            state, created = save_waitlist(c, cleaned)
            if state == "created":
                c.commit()
            else:
                c.rollback()
        finally:
            c.close()
        if state == "created":
            record = dict(cleaned)
            record["created"] = created
            notify_waitlist(record)
        if _wants_html(request, kind):
            return _html(render_landing(notice="You're on the waitlist."))
        return {"ok": True}

    @application.post("/api/reports/checkout", tags=["reports"], summary="Start Stripe Checkout for a personal report")
    async def reports_checkout(request: Request):
        if not payments_enabled():
            raise HTTPException(403, "payments are not enabled")
        data, kind = await _read_payload(request)
        try:
            _limit(request, "report")
        except HTTPException as exc:
            if not _wants_html(request, kind):
                raise
            return _html(render_landing(_values_from(data), notice="Too many checkout attempts. Try again later."), exc.status_code, exc.headers)
        cleaned, errors = validate_intake(data)
        if errors:
            if _wants_html(request, kind):
                return _html(render_landing(_values_from(data), errors), 422)
            return JSONResponse({"errors": errors}, status_code=422)
        token = secrets.token_urlsafe(32)
        now = time.time()
        c = db()
        try:
            ensure_tables(c)
            c.execute(
                "INSERT INTO career_reports(id, created, status, intake_json, updated) VALUES(?,?,?,?,?)",
                (token, now, "pending", json.dumps(cleaned, ensure_ascii=False), now),
            )
            c.commit()
        finally:
            c.close()
        base = public_base_url(request)
        try:
            session = create_checkout_session(
                token,
                base + "/reports/success?session_id={CHECKOUT_SESSION_ID}",
                base + "/reports?checkout=cancel",
                base,
            )
        except HTTPException:
            raise
        except Exception:
            print("stripe checkout create failed", flush=True)
            raise HTTPException(502, "Checkout is unavailable")
        sid = _field_get(session, "id")
        url = safe_checkout_url(_field_get(session, "url"))
        if not sid or not _SESSION.match(str(sid)) or not url:
            raise HTTPException(502, "Checkout is unavailable")
        c = db()
        try:
            c.execute(
                "UPDATE career_reports SET stripe_session_id=?, updated=? WHERE id=?",
                (sid, time.time(), token),
            )
            c.commit()
        finally:
            c.close()
        funnel.record_event("checkout_start", src=request.query_params.get("src"), report_id=token)
        if _wants_html(request, kind):
            body = (
                "<div class=\"card\"><h1>Continue to checkout</h1>"
                "<p>Stripe hosts the payment page. MindGuild does not see your card number.</p>"
                f"<div class=\"row\"><a class=\"btn\" href=\"{esc(url)}\">Continue to Stripe</a></div></div>"
            )
            return HTMLResponse(_shell("Checkout", body))
        return {"checkout_url": url, "session_id": sid}

    @application.post(
        "/api/reports/preview",
        tags=["reports"],
        summary="Admin preview of a personal report while reviewing the generator",
    )
    async def reports_preview(request: Request, authorization: Optional[str] = Header(None)):
        actor = appmod.auth_admin(authorization)
        data, _kind = await _read_payload(request)
        _limit(request, "report")
        cleaned, errors = validate_intake(data)
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)
        report = generate(cleaned)
        token = secrets.token_urlsafe(32)
        now = time.time()
        c = db()
        try:
            ensure_tables(c)
            c.execute(
                """INSERT INTO career_reports(id, created, status, intake_json, report_json, updated)
                   VALUES(?,?,?,?,?,?)""",
                (token, now, "preview", json.dumps(cleaned, ensure_ascii=False), json.dumps(report, ensure_ascii=False), now),
            )
            c.commit()
        finally:
            c.close()
        appmod.audit_admin(actor, "report_preview", target=token)
        return {"id": token, "url": "/reports/" + token}

    @application.get("/reports/success", include_in_schema=False)
    def reports_success(request: Request, session_id: str = ""):
        if not payments_enabled():
            raise HTTPException(404, "not found")
        _limit(request, "report")
        if not _SESSION.match(session_id or ""):
            raise HTTPException(404, "report not found")
        try:
            session = retrieve_checkout_session(session_id)
        except HTTPException:
            raise
        except Exception:
            print("stripe session lookup failed", flush=True)
            raise HTTPException(502, "Checkout is unavailable")
        if str(_field_get(session, "id") or "") != session_id:
            raise HTTPException(404, "report not found")
        try:
            report_id = unlock_paid_session(db, session, public_base_url(request))
        except HTTPException as exc:
            if exc.status_code == 402:
                page = _shell(
                    "Payment incomplete",
                    "<div class=\"card\"><h1>Payment is not complete</h1>"
                    "<p>The report stays locked until Stripe reports the payment as paid.</p>"
                    "<p><a href=\"/reports\">Back to Career Reports</a> · <a href=\"/refunds\">7-day refund policy</a></p></div>",
                )
                return _html(page, 402)
            raise
        return HTMLResponse(
            render_success(report_id),
            headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"},
        )

    @application.get("/refunds", include_in_schema=False)
    def refunds_page():
        return HTMLResponse(render_refunds())

    async def _stripe_webhook(request: Request):
        if not stripe_secret() or not stripe_price_id():
            raise HTTPException(404, "not found")
        payload = await request.body()
        signature = request.headers.get("stripe-signature") or ""
        event = parse_webhook(payload, signature)
        data = event.get("data", {}) if isinstance(event, dict) else _field_get(event, "data")
        obj = data.get("object") if isinstance(data, dict) else _field_get(data, "object")
        event_type = event.get("type") if isinstance(event, dict) else _field_get(event, "type")
        if event_type not in WEBHOOK_EVENTS:
            return {"ok": True, "ignored": True}
        sid = _field_get(obj, "id")
        if not sid or not _SESSION.match(str(sid)):
            raise HTTPException(400, "missing session")
        try:
            session = retrieve_checkout_session(str(sid))
        except HTTPException:
            raise
        except Exception:
            print("stripe webhook lookup failed", flush=True)
            raise HTTPException(502, "Checkout is unavailable")
        if str(_field_get(session, "id") or "") != str(sid):
            raise HTTPException(404, "report not found")
        return apply_checkout_event(db, event, session, public_base_url(request))

    @application.post("/api/stripe/webhook", tags=["reports"], summary="Stripe webhook; signature required")
    async def stripe_webhook(request: Request):
        return await _stripe_webhook(request)

    @application.post("/api/reports/stripe/webhook", tags=["reports"], summary="Stripe webhook alias; signature required")
    async def reports_webhook(request: Request):
        return await _stripe_webhook(request)

    @application.get("/reports/{report_id}/pdf", include_in_schema=False)
    def reports_pdf(report_id: str):
        loaded = _load_report(db, report_id)
        if not loaded:
            raise HTTPException(404, "report not found")
        kind, report = loaded
        blob = render_pdf(report, kind="preview" if kind == "preview" else "paid")
        return Response(blob, media_type="application/pdf", headers={
            "Content-Disposition": 'attachment; filename="mindguild-ai-career-report.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    @application.get("/reports/{report_id}", include_in_schema=False)
    def reports_one(report_id: str):
        loaded = _load_report(db, report_id)
        if not loaded:
            page = _shell(
                "Report not available",
                "<div class=\"card\"><h1>This report is not available</h1>"
                "<p>It may be unpaid, unknown, or already removed from the server database.</p>"
                "<p><a href=\"/reports\">Career Reports</a></p></div>",
                robots="noindex",
            )
            return _html(page, 404)
        kind, report = loaded
        page = render_report_page(report, kind=kind, pdf_path=f"/reports/{report_id}/pdf")
        return HTMLResponse(page, headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})
