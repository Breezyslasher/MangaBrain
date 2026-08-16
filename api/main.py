"""MangaBrain API entry point. Serves the SPA and the JSON API."""

import hmac
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.config import settings
from api.db import close_pool, ensure_schema, get_pool
from api.routers import (
    anilist,
    app_settings,
    exclusions,
    foryou,
    kitsu,
    mal,
    random_pick,
    recommend,
    search,
    yamtrack,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    get_pool()
    yield
    close_pool()


app = FastAPI(title="MangaBrain", version="0.1.0", lifespan=lifespan)


# Without Cache-Control, browsers heuristically cache the SPA assets and keep
# showing a stale UI after deployments until the user clears site data.
# no-cache means "store, but revalidate": unchanged files answer with a cheap
# 304 via ETag, changed files arrive immediately.
@app.middleware("http")
async def static_no_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path
    # .png and manifest.json matter here too: without revalidation headers
    # the browser heuristically caches app icons for a long time, so an icon
    # change keeps showing the old artwork until site data is cleared.
    if (
        path == "/"
        or path in MEDIUM_PATHS
        or path.endswith((".html", ".js", ".css", ".png", ".json"))
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Per-medium deep links, matching Anibrain's structure: each serves the SPA
# with that tab preselected (the JS reads the path). Registered as routes, so
# they win over the catch-all static mount.
MEDIUM_PATHS = ("/anime", "/manga", "/light-novel", "/one-shot")

# Everything under these prefixes is the JSON API; anything else is a static
# SPA asset. /healthz stays open for liveness probes and uptime monitors.
API_PREFIXES = (
    "/search",
    "/recommend",
    "/random",
    "/mal",
    "/anilist",
    "/foryou",
    "/exclusions",
    "/yamtrack",
    "/kitsu",
    "/settings",
    "/tags",
)

# Cover images load from the AniList CDN, hence img-src https:. No inline
# scripts or styles exist in the SPA; JS-assigned element.style is CSSOM and
# not restricted by style-src.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self';"
    " img-src 'self' https: data:; connect-src 'self'; worker-src 'self';"
    " frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

# Per-IP sliding request windows for the optional rate limit. In-process
# state: with several uvicorn workers each holds its own window, so the
# effective cap is limit * workers - fine for its abuse-damping purpose.
_rate_windows: dict[str, deque] = {}
_RATE_MAX_CLIENTS = 10_000


def _client_ip(request) -> str:
    # Behind a Cloudflare Tunnel every TCP peer is cloudflared on localhost;
    # CF-Connecting-IP carries the real client. Direct LAN requests lack it.
    return request.headers.get("CF-Connecting-IP") or (
        request.client.host if request.client else "unknown"
    )


def _rate_limited(ip: str, limit: int) -> bool:
    now = time.monotonic()
    window = _rate_windows.get(ip)
    if window is None:
        if len(_rate_windows) >= _RATE_MAX_CLIENTS:
            _rate_windows.clear()
        window = _rate_windows[ip] = deque()
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= limit:
        return True
    window.append(now)
    return False


@app.middleware("http")
async def security(request, call_next):
    path = request.url.path
    is_api = path.startswith(API_PREFIXES)

    if is_api and settings.rate_limit_per_minute > 0:
        if _rate_limited(_client_ip(request), settings.rate_limit_per_minute):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)

    if is_api and settings.auth_token:
        header = request.headers.get("Authorization", "")
        supplied = header.removeprefix("Bearer ").strip() if header else ""
        if not hmac.compare_digest(supplied, settings.auth_token):
            return JSONResponse({"detail": "missing or invalid access token"}, status_code=401)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if path == "/" or path in MEDIUM_PATHS or path.endswith(".html"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response


app.include_router(search.router)
app.include_router(recommend.router)
app.include_router(random_pick.router)
app.include_router(mal.router)
app.include_router(anilist.router)
app.include_router(foryou.router)
app.include_router(exclusions.router)
app.include_router(yamtrack.router)
app.include_router(kitsu.router)
app.include_router(app_settings.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _web_dir() -> Path | None:
    override = os.environ.get("WEB_DIR")
    candidates = (
        [Path(override)]
        if override
        else [
            Path(__file__).resolve().parent.parent / "web",
            Path.cwd() / "web",
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


web_dir = _web_dir()
if web_dir is not None:
    index_file = web_dir / "index.html"

    def _spa_shell() -> FileResponse:
        return FileResponse(index_file)

    for medium_path in MEDIUM_PATHS:
        app.add_api_route(medium_path, _spa_shell, include_in_schema=False)

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
