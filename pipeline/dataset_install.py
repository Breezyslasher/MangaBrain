"""Carry out the dataset installs the settings page asks for.

The API only records a request (api.datasets.request_install); this runs
it, in the worker, and reports as it goes so the settings page can show a
progress bar instead of a spinner. Called between nightly passes on a
one-minute poll, so a request made from a phone is picked up within a
minute rather than at the next 24-hourly pass.

Failures are sorted by whether waiting would help:

  - nothing to install yet (no release published) - retried hourly
  - the network or the host (a dropped download, GitHub down) - retried
    every six hours, so a transient failure heals without anyone noticing
  - anything else - a schema that does not match, a part that fails its
    checksum - stops as failed, and waits for someone to press Retry.
    Retrying those on a timer would re-download 3.5 GB every few hours to
    reach the same refusal.
"""

import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import psycopg

from api.config import settings
from api.datasets import BY_KEY, DATASETS, read_install, request_install, write_install

NOT_YET_RETRY = timedelta(hours=1)
NETWORK_RETRY = timedelta(hours=6)


class NotYet(Exception):
    """Nothing to install yet. Waiting will fix it."""


def _connect():
    return psycopg.connect(settings.database_url, autocommit=True)


def _due(state: dict) -> bool:
    if state.get("state") == "queued":
        return True
    if state.get("state") != "waiting":
        return False
    next_try = state.get("next_try")
    return next_try is None or datetime.fromisoformat(next_try) <= datetime.now(UTC)


def install_books(request: dict, report: Callable[[dict], None]) -> str:
    from pipeline import book_dataset

    url = book_dataset.find_release_manifest()
    if url is None:
        raise NotYet("No books release has been published yet; checking again hourly.")
    result = book_dataset.load(
        url,
        ensure=False,
        # Building the graph once, after, is faster than inserting into it
        # row by row - but it leaves searches without the index while it
        # builds. Worth it on a fresh install, which is serving nothing
        # yet; not on a live one, which keeps its index and pays per row.
        rebuild_indexes=bool(request.get("fresh")),
        progress=report,
    )
    return f"Installed {result['staged']:,} books ({result['embeddings']:,} with embeddings)."


INSTALLERS: dict[str, Callable[[dict, Callable[[dict], None]], str]] = {
    "books": install_books,
}


def request(key: str, fresh: bool = False) -> None:
    with _connect() as conn:
        request_install(conn, key, fresh=fresh)


def recover_interrupted() -> None:
    """A worker that died mid-install left it 'running' forever. Requeue it.

    Called once at startup, before anything can be running.
    """
    with _connect() as conn:
        for dataset in DATASETS:
            state = read_install(conn, dataset.key)
            if state and state.get("state") == "running":
                write_install(
                    conn, dataset.key, {**state, "state": "queued", "message": "Resuming"}
                )


def run(key: str) -> None:
    installer = INSTALLERS[key]
    with _connect() as conn:
        state = read_install(conn, key) or {}
        state = write_install(conn, key, {**state, "state": "running", "message": "Starting"})

        def report(progress: dict) -> None:
            nonlocal state
            state = write_install(conn, key, {**state, **progress, "state": "running"})

        try:
            message = installer(state, report)
        except NotYet as exc:
            write_install(conn, key, _waiting(state, str(exc), NOT_YET_RETRY))
        except (httpx.HTTPError, OSError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            write_install(
                conn, key, _waiting(state, f"Download failed ({reason}); retrying.", NETWORK_RETRY)
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            traceback.print_exc()
            write_install(
                conn,
                key,
                {**state, "state": "failed", "message": f"{type(exc).__name__}: {exc}"},
            )
        else:
            write_install(
                conn,
                key,
                {
                    "state": "done",
                    "message": message,
                    "fresh": state.get("fresh", False),
                    "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )


def _waiting(state: dict, message: str, after: timedelta) -> dict:
    next_try = (datetime.now(UTC) + after).isoformat(timespec="seconds")
    return {**state, "state": "waiting", "message": message, "next_try": next_try}


def run_queued() -> int:
    """Run every install that is due. Never raises; returns how many ran."""
    ran = 0
    try:
        with _connect() as conn:
            due = [
                key
                for key in INSTALLERS
                if (state := read_install(conn, key)) is not None and _due(state)
            ]
    except Exception:  # noqa: BLE001 - the nightly pass must outlive this
        traceback.print_exc()
        return 0
    for key in due:
        print(f"[datasets] installing {BY_KEY[key].label}", flush=True)
        run(key)
        ran += 1
    return ran
