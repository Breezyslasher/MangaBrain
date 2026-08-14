"""MangaBrain API entry point. Serves the SPA and the JSON API."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
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
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
