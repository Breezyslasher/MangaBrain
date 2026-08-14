"""MangaBrain API entry point. Serves the SPA and the JSON API."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.db import close_pool, get_pool
from api.routers import mal, random_pick, recommend, search


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_pool()
    yield
    close_pool()


app = FastAPI(title="MangaBrain", version="0.1.0", lifespan=lifespan)

app.include_router(search.router)
app.include_router(recommend.router)
app.include_router(random_pick.router)
app.include_router(mal.router)


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
