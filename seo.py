"""Canonical URLs, robots.txt, and sitemap.xml.

Every absolute URL is built from PUBLIC_BASE_URL (see reports.public_base_url).
This module does not name a host. Pages served on any host advertise that
configured origin as canonical.
"""

import re
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response

# Pages that belong in the sitemap, in addition to public bot profiles and
# visible Problem Labs. Query strings are not part of these paths.
SITEMAP_PATHS = (
    "/",
    "/reports",
    "/pricing",
    "/partners",
    "/ads",
    "/refunds",
    "/search",
)

_HEAD = re.compile(r"<head\b[^>]*>", re.I)
_HANDLE = re.compile(r"\A[A-Za-z0-9_]{1,40}\Z")
_installed = False


def canonical_url(base, path):
    """Absolute URL for a path. The query string is not included."""
    origin = (base or "").rstrip("/")
    origin = "".join(ch for ch in origin if 32 <= ord(ch) < 127)
    text = path or "/"
    text = text.split("?", 1)[0]
    text = "".join(ch for ch in text if 32 <= ord(ch) < 127)
    if not text.startswith("/"):
        text = "/" + text
    return origin + text


def profile_url(base, handle):
    """Public bot profile. The site addresses profiles as /#/handle."""
    if not isinstance(handle, str) or not _HANDLE.match(handle):
        return ""
    return canonical_url(base, "/") + "#/" + quote(handle, safe="_")


def inject_canonical(html, url):
    """Insert escaped canonical and og:url tags immediately after <head>."""
    if not html or 'rel="canonical"' in html:
        return html
    match = _HEAD.search(html)
    if not match:
        return html
    safe = xml_escape(url, {'"': "&quot;", "'": "&#39;"})
    tags = (
        f'<link rel="canonical" href="{safe}">'
        f'<meta property="og:url" content="{safe}">'
    )
    end = match.end()
    return html[:end] + tags + html[end:]


def robots_body(base):
    sitemap = canonical_url(base, "/sitemap.xml")
    return (
        "User-agent: *\n"
        "Disallow: /admin\n"
        "Disallow: /api\n"
        "\n"
        "Sitemap: " + sitemap + "\n"
    )


def sitemap_body(base, handles, problem_ids):
    locs = [canonical_url(base, path) for path in SITEMAP_PATHS]
    for handle in handles:
        url = profile_url(base, handle)
        if url:
            locs.append(url)
    for pid in problem_ids:
        locs.append(canonical_url(base, "/problems/" + str(int(pid))))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc in locs:
        lines.append("<url><loc>" + xml_escape(loc) + "</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _base(request):
    import reports

    return reports.public_base_url(request)


def _public_html(request, response):
    if request.method not in ("GET", "HEAD"):
        return False
    path = request.url.path or "/"
    if path.startswith("/admin") or path.startswith("/api"):
        return False
    if response.status_code not in (200, 404):
        return False
    ctype = (response.headers.get("content-type") or "").lower()
    return "text/html" in ctype


async def _read_body(response):
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, dict) and chunk.get("type") == "http.response.pathsend":
            file_path = chunk.get("path")
            if file_path:
                with open(file_path, "rb") as handle:
                    chunks.append(handle.read())
            continue
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        elif isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
    return b"".join(chunks)


def _html_response(response, text):
    headers = {}
    for key, value in response.headers.items():
        if key.lower() in ("content-length", "content-type"):
            continue
        headers[key] = value
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


class CanonicalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # FastAPI does not answer HEAD for a GET route. Run the GET handler so
        # public pages, robots.txt, and sitemap.xml answer both methods.
        if request.method == "HEAD":
            request.scope["method"] = "GET"
        response = await call_next(request)
        if not _public_html(request, response):
            return response
        body = await _read_body(response)
        text = body.decode("utf-8", "replace")
        url = canonical_url(_base(request), request.url.path or "/")
        updated = inject_canonical(text, url)
        return _html_response(response, updated)


def install(application):
    global _installed
    if _installed:
        return

    def robots(request: Request):
        return PlainTextResponse(robots_body(_base(request)))

    def sitemap(request: Request):
        import app as appmod

        c = appmod.db()
        try:
            gone = appmod.PROBLEM_GONE
            bots = c.execute(
                "SELECT handle FROM bots WHERE banned=0 AND handle!='sponsored' ORDER BY handle"
            ).fetchall()
            marks = ",".join("?" * len(gone))
            problems = c.execute(
                f"SELECT id FROM problems WHERE status NOT IN ({marks}) ORDER BY id",
                gone,
            ).fetchall()
        finally:
            c.close()
        body = sitemap_body(
            _base(request),
            [row["handle"] for row in bots],
            [row["id"] for row in problems],
        )
        return Response(body, media_type="application/xml; charset=utf-8")

    application.add_api_route("/robots.txt", robots, methods=["GET"], include_in_schema=False)
    application.add_api_route("/sitemap.xml", sitemap, methods=["GET"], include_in_schema=False)
    application.add_middleware(CanonicalMiddleware)
    _installed = True
