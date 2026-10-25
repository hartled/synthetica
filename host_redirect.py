"""Redirect requests for a non-canonical host to PUBLIC_BASE_URL.

The old Render hostname was still returning the full site, so search had two
copies. Any public host other than mindguild.dev or www.mindguild.dev is sent
to the same path and query on the origin in PUBLIC_BASE_URL.

www.mindguild.dev stays served. Canonical tags and sitemap.xml are already
built only from PUBLIC_BASE_URL (https://mindguild.dev in production), so a
page on www advertises the apex. Redirecting www would disagree with that
allowlist. The onrender host is what moves.

GET and HEAD use 301. Every other method uses 308 so the method and body
survive. /healthz and both Stripe webhook paths stay on whatever host called
them. localhost, 127.0.0.1, testserver, and other loopback names stay too,
so local uvicorn and the test client do not redirect.

PUBLIC_BASE_URL is read with the same origin check as report links. When it
is unset, invalid, or not a mindguild host, the redirect is skipped and a
misconfigured origin is logged once. Following it would loop or publish the
wrong host. On Render, X-Forwarded-Host is the public host when the proxy
sends it. That header is ignored unless RENDER is set, same as the other
forwarded client data this process trusts.
"""

import ipaddress
import os

from starlette.responses import RedirectResponse

# Both answer 200. The redirect target is whichever one PUBLIC_BASE_URL names.
_CANONICAL_HOSTS = frozenset({"mindguild.dev", "www.mindguild.dev"})
# Render's health check, plus the webhook URL registered in Stripe and the
# older alias that runs the same handler. A redirect would drop the POST body
# Stripe sends to the host it has on file.
_EXEMPT_PATHS = frozenset({
    "/healthz",
    "/api/stripe/webhook",
    "/api/reports/stripe/webhook",
})
_LOCAL_NAMES = frozenset({
    "localhost",
    "testserver",
    "host.docker.internal",
    "localhost.localdomain",
})
_warned_origins = set()
_installed = False


def _scope_header(scope, name):
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1", "replace")
    return ""


def host_label(value):
    """Hostname only: no port, no trailing dot, lower case, first forwarded value."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = text.split(",")[0].strip().rstrip(".")
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            return text[1:end].rstrip(".")
        return ""
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            return host.rstrip(".")
    return text


def _render():
    return os.environ.get("RENDER", "").strip().lower() in ("1", "true", "yes")


def request_host(scope):
    """Host the client asked for.

    Uvicorn's --proxy-headers rewrites the scheme and client address, not the
    host. When RENDER is set and the proxy sent X-Forwarded-Host, that value
    is the public host. It is ignored otherwise, so a caller cannot hide the
    old hostname behind a spoofed forwarded host. The middleware also refuses
    to redirect when the Host header itself is already canonical: following
    X-Forwarded-Host in that case would send the browser back to the same URL.
    """
    direct = host_label(_scope_header(scope, b"host"))
    if _render():
        forwarded = host_label(_scope_header(scope, b"x-forwarded-host"))
        if forwarded:
            return forwarded
    return direct


def is_local_host(host):
    if not host or host in _LOCAL_NAMES:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_unspecified)


def configured_origin():
    """PUBLIC_BASE_URL when it is a clean origin, else empty.

    Does not fall back to the Render hostname. That fallback is the host we
    are trying to retire, and redirecting to it would loop.
    """
    import reports

    return reports._origin(os.environ.get("PUBLIC_BASE_URL"))


def _origin_host(origin):
    return host_label(origin.split("://", 1)[-1])


def _warn_skipped(origin):
    if origin in _warned_origins:
        return
    _warned_origins.add(origin)
    print(
        "WARNING: PUBLIC_BASE_URL is "
        + origin
        + ", which is not mindguild.dev or www.mindguild.dev. "
        + "Skipping the canonical host redirect so a misconfigured origin cannot loop.",
        flush=True,
    )


def redirect_origin():
    """Origin to send other hosts to, or empty when the redirect must not run."""
    origin = configured_origin()
    if not origin:
        return ""
    if _origin_host(origin) not in _CANONICAL_HOSTS:
        _warn_skipped(origin)
        return ""
    return origin


def redirect_location(origin, raw_path, query):
    if isinstance(raw_path, (bytes, bytearray)):
        path = raw_path.decode("latin-1", "replace")
    else:
        path = str(raw_path or "")
    if isinstance(query, (bytes, bytearray)):
        query = query.decode("latin-1", "replace")
    else:
        query = str(query or "")
    path = path.replace("\r", "").replace("\n", "").replace("\x00", "")
    query = query.replace("\r", "").replace("\n", "").replace("\x00", "")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        path = "/"
    location = origin + path
    if query:
        location += "?" + query
    return location


class CanonicalHostMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        origin = redirect_origin()
        if not origin:
            await self.app(scope, receive, send)
            return
        # Host is what the client will send again. Redirecting away from a
        # canonical or local Host loops or breaks dev, even if a forwarded
        # host says something else.
        direct = host_label(_scope_header(scope, b"host"))
        public = request_host(scope)
        origin_host = _origin_host(origin)
        if (
            is_local_host(direct)
            or direct in _CANONICAL_HOSTS
            or direct == origin_host
            or is_local_host(public)
            or public in _CANONICAL_HOSTS
            or public == origin_host
        ):
            await self.app(scope, receive, send)
            return
        location = redirect_location(origin, scope.get("raw_path") or path, scope.get("query_string") or b"")
        status = 301 if scope.get("method") in ("GET", "HEAD") else 308
        response = RedirectResponse(location, status_code=status)
        await response(scope, receive, send)


def install(application):
    global _installed
    if _installed:
        return
    application.add_middleware(CanonicalHostMiddleware)
    _installed = True
