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

# How often an installed books catalogue looks for a newer release. The
# books workflow publishes monthly, so daily is plenty; one GitHub API call.
UPDATE_CHECK_INTERVAL = timedelta(hours=20)
UPDATE_CHECK_KEY = "books_update_check"


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


def install_tmdb(request: dict, report: Callable[[dict], None]) -> str:
    """Films and TV: fetched from TMDB by this install, never downloaded
    as a release (TMDB's terms rule out publishing one). Resumable, so a
    worker restart mid-fetch continues where it stopped."""
    from pipeline import sync_tmdb

    with psycopg.connect(settings.database_url) as conn:
        key = sync_tmdb.stored_key(conn)
        if not key:
            raise NotYet("Add a TMDB API key in Settings > Server; checking again hourly.")
        client = sync_tmdb.TmdbClient(key)
        try:
            tally = sync_tmdb.full(conn, client, progress=report)
            # Asked for again after the full pass is done ("Check TMDB for
            # changes now"): the full pass skips completed kinds, so run the
            # changes feed the nightly pass would.
            if not tally:
                tally = sync_tmdb.changes(conn, client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise RuntimeError(
                    "TMDB rejected the API key; check it in Settings > Server"
                ) from exc
            raise
        finally:
            client.close()
        count = conn.execute(
            "SELECT count(*) FROM media WHERE source = %s", (sync_tmdb.SOURCE,)
        ).fetchone()[0]
    return f"{count:,} films and shows from TMDB ({tally.get('kept', 0):,} this pass)."


def install_tvmaze(request: dict, report: Callable[[dict], None]) -> str:
    """TV: the newest published TV release first, if there is one this
    database has not loaded - it carries the embeddings, hours of NAS work
    - then a full TVmaze read to top it up, a few minutes. With no release
    yet, or GitHub unreachable, the read alone does the job."""
    from pipeline import sync_tvmaze, tv_dataset
    from pipeline.sync_anilist import get_state

    loaded = ""
    try:
        url = tv_dataset.find_release()
    except httpx.HTTPError:
        url = None
    if url is not None:
        with _connect() as conn:
            done = (get_state(conn, tv_dataset.LOADED_RELEASE_KEY) or {}).get("manifest")
        if done != url:
            result = tv_dataset.load(url, progress=report)
            loaded = f" Loaded {result['embeddings']:,} embeddings from the published release."
    tally = sync_tvmaze.sync(progress=report)
    return f"Fetched {tally['kept']:,} TV shows from TVmaze ({tally['read']:,} read).{loaded}"


INSTALLERS: dict[str, Callable[[dict, Callable[[dict], None]], str]] = {
    "books": install_books,
    "tmdb": install_tmdb,
    "tvmaze": install_tvmaze,
}


def check_for_updates() -> bool:
    """Queue a books update when a newer release is out. Never raises.

    The books counterpart of the nightly AniList sync. AniList is asked
    for what changed; Open Library has no such API, only a monthly dump,
    so the books workflow imports and embeds each dump on GitHub and this
    loads the result. The load merges on (source, source_id): new books
    arrive with their embeddings, changed ones are updated in place, and
    nothing is deleted.

    Only for a database that has books. One whose Books tab was never
    switched on must not start a 3.5 GB download on its own; it gets
    books through the settings page, which makes the same request.

    A database with books but no record of a release (imported locally
    with pipeline.sync_openlibrary) takes the first release it sees,
    which is how a local import gets its embeddings without spending
    days of NAS CPU on them. Returns True if an update was queued.
    """
    from pipeline import book_dataset
    from pipeline.sync_anilist import get_state, set_state

    try:
        with _connect() as conn:
            checked = (get_state(conn, UPDATE_CHECK_KEY) or {}).get("checked_at")
            recent = datetime.now(UTC) - UPDATE_CHECK_INTERVAL
            if checked and datetime.fromisoformat(checked) > recent:
                return False
            has_books = conn.execute(
                "SELECT 1 FROM media WHERE source = 'openlibrary' LIMIT 1"
            ).fetchone()
            if has_books is None:
                return False
            current = read_install(conn, "books") or {}
            if current.get("state") in ("queued", "running", "waiting"):
                return False
            url = book_dataset.find_release_manifest()
            set_state(
                conn,
                UPDATE_CHECK_KEY,
                {
                    "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
            if url is None:
                return False
            loaded = get_state(conn, book_dataset.LOADED_RELEASE_KEY) or {}
            if loaded.get("manifest") == url:
                return False
            request_install(conn, "books")
            write_install(
                conn,
                "books",
                {
                    **(read_install(conn, "books") or {}),
                    "message": "A newer books release is out; updating",
                },
            )
            print(f"[datasets] newer books release found; queued {url}", flush=True)
            return True
    except Exception:  # noqa: BLE001 - the nightly pass must outlive this
        traceback.print_exc()
        return False


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
