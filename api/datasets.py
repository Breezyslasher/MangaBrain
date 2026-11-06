"""The datasets an install can hold, and which media each one provides.

This is the one place a new medium is declared. Add a Dataset here with
the media groups it fills, the `source` its rows carry, and an installer
in pipeline.dataset_install, and both clients pick it up: the settings
page lists it with its size, a reader switches its media on, and the
worker downloads it. Nothing else needs to learn its name.

Installs are requested by the API and carried out by the worker. A load
can take hours - the books release is 3.5 GB, and on a live install its
vectors go into an HNSW index one at a time - which no HTTP request
should be held open for, and which the API process has no business
running. The request and every progress report travel through
sync_state, which both processes already read.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    description: str
    # The medium groups (tabs) this dataset fills.
    media: tuple[str, ...]
    # The media.source value its rows carry, which is how it is counted.
    source: str
    # What installing it costs, shown before anyone is asked to agree.
    download_hint: str
    # False for a dataset the worker maintains on its own and that cannot
    # be absent - AniList is synced every night whatever is selected.
    installable: bool
    # Counted as present even with no rows: the worker fills it by itself,
    # every night, whether or not anyone asked. AniList is; its tabs must
    # show from a fresh install's first page, before the first sync lands.
    # Kept apart from installable, which only says a reader may ask for it -
    # the two were one flag until the anime dataset became reloadable, and
    # a fresh install then showed no tabs at all.
    always_present: bool = False
    # What asking for the install again does, as the settings page labels
    # it. Books load a published release; TV and films have none and are
    # fetched from their source, so "the newest release" would be wrong.
    update_label: str = "Update from the newest release"


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="anilist",
        label="Anime, manga and light novels",
        description=(
            "From AniList, including manhwa, manhua and one-shots. Always present: the"
            " worker keeps it current every night."
        ),
        media=("anime", "manga", "light_novel", "one_shot"),
        source="anilist",
        download_hint="About 1 GB, with the embeddings.",
        # Installable so it can be reloaded: the weekly release now also
        # carries a merge-format copy (pipeline.anilist_dataset) that loads
        # over a live install without touching books, TV or films. The
        # worker still syncs AniList nightly whatever is selected.
        installable=True,
        always_present=True,
        update_label="Reload from the newest release",
    ),
    Dataset(
        key="books",
        label="Books",
        description=(
            "From Open Library: about 930,000 works with a real description and at least"
            " one subject, with their embeddings."
        ),
        media=("book",),
        source="openlibrary",
        download_hint="About 3.5 GB to download and 6 GB on disk.",
        installable=True,
    ),
    Dataset(
        key="tvmaze",
        label="TV shows",
        description=(
            "From TVmaze (tvmaze.com), licensed CC BY-SA: about 51,000 shows with a real"
            " summary, Japanese animation left to the anime tab. Read in full every night."
        ),
        media=("tv",),
        source="tvmaze",
        download_hint="A few minutes of TVmaze requests, then embedding on this machine.",
        installable=True,
        update_label="Fetch again from TVmaze now",
    ),
    Dataset(
        key="tmdb",
        label="Films",
        description=(
            "From TMDB, fetched with your own API key (Settings > Server): about 520,000"
            " films with a real overview, plus the TV shows TVmaze does not have. TMDB's"
            " terms rule out publishing it, so this install fetches it itself and"
            " refreshes it nightly. This product uses TMDB and the TMDB APIs but is not"
            " endorsed, certified, or otherwise approved by TMDB."
        ),
        media=("movie",),
        source="tmdb",
        download_hint=(
            "About 1.4 million TMDB requests, roughly ten hours, then embedding on this"
            " machine. Needs a free TMDB API key."
        ),
        installable=True,
        update_label="Check TMDB for changes now",
    ),
)

BY_KEY = {d.key: d for d in DATASETS}


def dataset_for_medium(group: str) -> Dataset | None:
    for dataset in DATASETS:
        if group in dataset.media:
            return dataset
    return None


# Every group belongs to exactly one dataset. tests/test_datasets.py holds
# this registry against MEDIUM_GROUPS, so a new tab cannot arrive without a
# way to fill it - checked there rather than asserted here, where a drift
# would take the whole API down at import.


# ---------------------------------------------------------------------
# Install requests and progress, in sync_state.
#
# One row per dataset, key "dataset_install:<key>", holding:
#   state     queued | running | waiting | done | failed
#   phase     downloading | merging | indexing (while running)
#   bytes, total   download progress
#   message   what a reader should be told
#   fresh     a fresh install, so the vector index is built after the load
#   next_try  ISO time before which a waiting install is not retried
#
# waiting means asked for but not yet available - no release published,
# or a download that failed and will be retried - so it is retried on a
# timer rather than on every poll, which would be a GitHub API call a
# minute.

ACTIVE = ("queued", "running", "waiting")


def state_key(key: str) -> str:
    return f"dataset_install:{key}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


READ_SQL = "SELECT value FROM sync_state WHERE key = %s"
WRITE_SQL = """
    INSERT INTO sync_state (key, value, updated_at) VALUES (%s, %s::jsonb, now())
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""


def _value(row) -> dict | None:
    if row is None:
        return None
    value = row["value"] if isinstance(row, dict) else row[0]
    return value if isinstance(value, dict) else json.loads(value)


def read_install(conn, key: str) -> dict | None:
    return _value(conn.execute(READ_SQL, (state_key(key),)).fetchone())


def write_install(conn, key: str, value: dict) -> dict:
    value = {**value, "updated_at": now_iso()}
    conn.execute(WRITE_SQL, (state_key(key), json.dumps(value)))
    return value


def request_install(conn, key: str, fresh: bool = False) -> dict:
    """Queue an install unless one is already under way. Idempotent."""
    current = read_install(conn, key)
    if current and current.get("state") in ACTIVE:
        return current
    return write_install(
        conn,
        key,
        {"state": "queued", "fresh": fresh, "requested_at": now_iso(), "message": "Queued"},
    )


COUNTS_SQL = """
    SELECT m.source, count(*) AS items, count(e.media_id) AS embedded
    FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %s
    GROUP BY m.source
"""


def counts(conn, model_id: str) -> dict[str, tuple[int, int]]:
    rows = conn.execute(COUNTS_SQL, (model_id,)).fetchall()
    out = {}
    for row in rows:
        if isinstance(row, dict):
            out[row["source"]] = (row["items"], row["embedded"])
        else:
            out[row[0]] = (row[1], row[2])
    return out
