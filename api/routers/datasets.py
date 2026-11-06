"""The datasets this install holds and can hold, and installing them.

GET /datasets lists every dataset in api.datasets with what is present,
what an install would cost and how a running one is going. POST
/datasets/{key}/install asks the worker for one - usually made for you by
switching a medium on in the settings page, but here for a Retry button
too. The work itself happens in the worker (pipeline.dataset_install).
"""

import threading
import time

from fastapi import APIRouter, HTTPException

from api.config import embed_model_id, settings
from api.datasets import BY_KEY, DATASETS, Dataset, counts, read_install, request_install
from api.db import get_pool
from api.models import DatasetInstallOut, DatasetOut, DatasetsOut

router = APIRouter()

# Counting walks the whole catalogue - seconds on a NAS at 1.2 million
# rows - and the settings page polls while an install runs, while
# GET /settings needs the counts to know which media are present. So a
# count older than COUNTS_TTL is served as it is and refreshed on a
# background thread: no request waits for a count except the very first.
COUNTS_TTL = 60.0
# A GitHub API call, shared by IP when unauthenticated.
RELEASE_TTL = 900.0
_counts_cache: tuple[float, dict] | None = None
_release_cache: dict[str, tuple[float, bool | None]] = {}


_refreshing = threading.Lock()


def _refresh_counts() -> None:
    global _counts_cache
    try:
        with get_pool().connection() as conn:
            _counts_cache = (
                time.monotonic(),
                counts(conn, embed_model_id(settings.embed_model)),
            )
    except Exception:  # noqa: BLE001 - the stale counts stay; next call retries
        pass
    finally:
        _refreshing.release()


def catalogue_counts(conn) -> dict[str, tuple[int, int]]:
    global _counts_cache
    if _counts_cache is None:
        # Nothing to serve yet: the one request that waits.
        _counts_cache = (time.monotonic(), counts(conn, embed_model_id(settings.embed_model)))
    elif time.monotonic() - _counts_cache[0] > COUNTS_TTL and _refreshing.acquire(blocking=False):
        threading.Thread(target=_refresh_counts, name="dataset-counts", daemon=True).start()
    return _counts_cache[1]


def forget_counts() -> None:
    global _counts_cache
    _counts_cache = None


def release_available(dataset: Dataset) -> bool | None:
    if dataset.key != "books":
        return None
    now = time.monotonic()
    cached = _release_cache.get(dataset.key)
    if cached and now - cached[0] < RELEASE_TTL:
        return cached[1]
    try:
        from pipeline.book_dataset import find_release_manifest

        found: bool | None = find_release_manifest() is not None
    except Exception:  # noqa: BLE001 - unknown, not an error page
        found = None
    _release_cache[dataset.key] = (now, found)
    return found


def installed_keys(conn) -> set[str]:
    present = catalogue_counts(conn)
    return {d.key for d in DATASETS if d.always_present or present.get(d.source, (0, 0))[0] > 0}


def to_out(conn, dataset: Dataset) -> DatasetOut:
    items, embedded = catalogue_counts(conn).get(dataset.source, (0, 0))
    install = read_install(conn, dataset.key)
    return DatasetOut(
        key=dataset.key,
        label=dataset.label,
        description=dataset.description,
        media=list(dataset.media),
        download_hint=dataset.download_hint,
        installable=dataset.installable,
        installed=dataset.always_present or items > 0,
        items=items,
        embedded=embedded,
        install=DatasetInstallOut(
            **{k: install.get(k) for k in DatasetInstallOut.model_fields if k in install}
        )
        if install
        else None,
        release_available=release_available(dataset) if dataset.installable else None,
        update_label=dataset.update_label,
    )


@router.get("/datasets", response_model=DatasetsOut)
def list_datasets() -> DatasetsOut:
    with get_pool().connection() as conn:
        return DatasetsOut(datasets=[to_out(conn, d) for d in DATASETS])


@router.post("/datasets/{key}/install", response_model=DatasetOut)
def install_dataset(key: str) -> DatasetOut:
    dataset = BY_KEY.get(key)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"no dataset called {key!r}")
    if not dataset.installable:
        raise HTTPException(
            status_code=400, detail=f"{dataset.label} is maintained by the worker, not installed"
        )
    with get_pool().connection() as conn:
        request_install(conn, key)
        return to_out(conn, dataset)
