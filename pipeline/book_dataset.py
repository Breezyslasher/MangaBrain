"""The books dataset: export, load, and the state a CI build carries.

Separate from pipeline.snapshot and pipeline.restore on purpose, for
three reasons that each rule the existing pair out.

  1. Size. A 768-dim float32 embedding is 3,072 bytes; times roughly
     930,000 books that is ~2.9 GB of vectors before a single
     description, and GitHub caps a release asset at 2 GB. Floats barely
     compress. So the export is one gzip stream cut into parts, each
     under the cap, with a manifest naming them in order.

  2. Merge, not replace. pipeline.restore runs pg_restore --clean, which
     drops and recreates whole tables. Restoring the anime dataset over a
     books catalogue would delete every book, and the reverse would
     delete every anime. This loads into a staging table and upserts on
     (source, source_id), touching openlibrary rows only, so the two
     datasets can land in one database in either order.

  3. Rows, not tables. pg_dump exports whole tables and cannot filter.
     This exports with a COPY over a query that selects books alone.

The format is binary COPY, gzipped. Binary because a vector in text form
is about 7.7 KB rather than 3 KB, and because the binary format refuses
a type it does not expect rather than coercing it. The manifest also
records every column's type, and a load compares them against the
target before reading a byte of data.

Ids are local. Books take their ids from media_external_id_seq, so the
same work has a different id in every database; the export carries
(source, source_id) and the load resolves ids on arrival. It also only
draws ids for rows that are new - an upsert with nextval in its VALUES
spends one per row whether it inserts or not, which on a 32-bit id
column is a budget worth not wasting.

    python -m pipeline.book_dataset export --out book-dataset
    python -m pipeline.book_dataset load --release latest
    python -m pipeline.book_dataset load --dir book-dataset
    python -m pipeline.book_dataset load --manifest-url https://.../mangabrain-books.manifest.json
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

import httpx

FORMAT = "mangabrain-books/1"
MANIFEST_NAME = "mangabrain-books.manifest.json"
PART_PREFIX = "mangabrain-books.copy.gz.part"
DEFAULT_REPO = "Breezyslasher/MangaBrain"
RELEASE_TAG_PREFIX = "books-"

# Under GitHub's 2 GiB asset cap with room to spare. A part is a byte
# range of one gzip stream, not a valid gzip file of its own: the parts
# are concatenated in manifest order and decompressed as one.
PART_BYTES = 1_900_000_000

SOURCE = "openlibrary"

# Exactly the columns pipeline.sync_openlibrary writes, plus the
# identity it writes them under. Anything else on a book row is local
# and is left alone by a load.
BOOK_COLUMNS = (
    "source",
    "source_id",
    "media_type",
    "medium",
    "title_romaji",
    "description",
    "description_clean",
    "genres",
    "tags",
    "start_year",
    "cover_image_medium",
    "cover_image_large",
    "synonyms",
    "synced_at",
)
# The columns an update may overwrite. Identity is matched, not set.
UPDATE_COLUMNS = tuple(c for c in BOOK_COLUMNS if c not in ("source", "source_id"))


def select_sql(model_literal: str, where: str) -> str:
    cols = ", ".join(f"m.{c}" for c in BOOK_COLUMNS)
    return (
        f"SELECT {cols}, e.embedding FROM media m"
        f" LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = {model_literal}"
        f" WHERE {where}"
    )


# Built from the target's own schema, so its types are the target's.
# WHERE false: the shape is wanted, never the rows.
STAGE_SQL = "CREATE TEMP TABLE book_stage AS " + select_sql("''", "false")

STAGE_TYPES_SQL = """
    SELECT a.attname, format_type(a.atttypid, a.atttypmod)
    FROM pg_attribute a
    WHERE a.attrelid = 'book_stage'::regclass AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
"""

# Only where the dataset is newer. A database that imported a later dump
# than the dataset was built from must not have its descriptions rolled
# back by a load - that would be an older catalogue passing as a newer one.
UPDATE_SQL = (
    "UPDATE media m SET "
    + ", ".join(f"{c} = s.{c}" for c in UPDATE_COLUMNS)
    + " FROM book_stage s WHERE m.source = s.source AND m.source_id = s.source_id"
    " AND (m.synced_at IS NULL OR m.synced_at < s.synced_at)"
)

INSERT_SQL = (
    "INSERT INTO media (id, "
    + ", ".join(BOOK_COLUMNS)
    + ") SELECT nextval('media_external_id_seq'), "
    + ", ".join(f"s.{c}" for c in BOOK_COLUMNS)
    + " FROM book_stage s WHERE NOT EXISTS ("
    "SELECT 1 FROM media m WHERE m.source = s.source AND m.source_id = s.source_id)"
)

EMBEDDINGS_SQL = """
    INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)
    SELECT m.id, %(model)s, s.embedding, now()
    FROM book_stage s
    JOIN media m ON m.source = s.source AND m.source_id = s.source_id
    WHERE s.embedding IS NOT NULL
    ON CONFLICT (media_id, embed_model) DO NOTHING
"""
# DO NOTHING, not an update: a vector this database already holds was
# computed here, from this database's own text, and replacing it with the
# dataset's buys nothing. It also spares a NAS from rewriting gigabytes of
# vectors - and an HNSW index entry for each - on every load.

PENDING_SQL = """
    SELECT count(*) FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.source = 'openlibrary' AND e.media_id IS NULL
"""

COUNTS_SQL = """
    SELECT count(*), count(e.media_id) FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.source = 'openlibrary'
"""

# The vector indexes, by the names db/schema.sql gives them. A CI build
# drops them: it never searches, and an HNSW index costs every insert.
# A build reloads its whole checkpoint at the start of each chained job,
# so with the index in place every job would pay to rebuild the graph
# for every book embedded so far.
VECTOR_INDEXES = ("idx_embeddings_hnsw_384", "idx_embeddings_hnsw_768")


# ---------------------------------------------------------------------
# State that travels with a build: which dump it was made from, whether
# the import of that dump finished, and which dump the last published
# release carried. It rides inside the manifest, so a checkpoint and a
# release are one format and either can resume the other.


def read_state(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text())


def write_state(path: Path | None, state: dict) -> None:
    if path is not None:
        path.write_text(json.dumps(state, indent=2, sort_keys=True))


def current_model_id() -> str:
    from api.config import embed_model_id, settings

    return embed_model_id(settings.embed_model)


# ---------------------------------------------------------------------
# Export


class PartWriter(io.RawIOBase):
    """Bytes in, a sequence of capped part files out, hashed as they go."""

    def __init__(self, out_dir: Path, part_bytes: int = PART_BYTES):
        self.out_dir = out_dir
        self.part_bytes = part_bytes
        self.parts: list[dict] = []
        self._fh = None
        self._hash = None
        self._written = 0

    def writable(self) -> bool:
        return True

    def _open_next(self) -> None:
        self._close_current()
        name = f"{PART_PREFIX}{len(self.parts) + 1:02d}"
        self._fh = (self.out_dir / name).open("wb")
        self._hash = hashlib.sha256()
        self._written = 0
        self.parts.append({"name": name})

    def _close_current(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self.parts[-1]["bytes"] = self._written
            self.parts[-1]["sha256"] = self._hash.hexdigest()
            self._fh = None

    def write(self, data) -> int:
        view = memoryview(data)
        total = len(view)
        while view:
            if self._fh is None or self._written >= self.part_bytes:
                self._open_next()
            room = self.part_bytes - self._written
            chunk = view[:room]
            self._fh.write(chunk)
            self._hash.update(chunk)
            self._written += len(chunk)
            view = view[len(chunk) :]
        return total

    def close(self) -> None:
        self._close_current()
        super().close()


def column_types(conn) -> list[list[str]]:
    conn.execute("DROP TABLE IF EXISTS book_stage")
    conn.execute(STAGE_SQL)
    types = [list(r) for r in conn.execute(STAGE_TYPES_SQL).fetchall()]
    conn.execute("DROP TABLE book_stage")
    return types


def export(out_dir: Path, state_path: Path | None = None, part_bytes: int = PART_BYTES) -> dict:
    import psycopg
    from psycopg import sql

    from api.config import settings

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{PART_PREFIX}*"):
        stale.unlink()
    model = current_model_id()
    started = time.monotonic()
    with psycopg.connect(settings.database_url) as conn:
        types = column_types(conn)
        rows, embedded = conn.execute(COUNTS_SQL, {"model": model}).fetchone()
        query = (
            "COPY ("
            + select_sql(
                sql.Literal(model).as_string(conn),
                "m.source = 'openlibrary' ORDER BY m.source_id",
            )
            + ") TO STDOUT (FORMAT binary)"
        )
        writer = PartWriter(out_dir, part_bytes)
        with (
            gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=6, mtime=0) as gz,
            conn.cursor() as cur,
            cur.copy(query) as copy,
        ):
            for data in copy:
                gz.write(data)
        writer.close()

    manifest = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embed_model": model,
        "rows": rows,
        "embedded": embedded,
        "columns": types,
        "compression": "gzip",
        "parts": writer.parts,
        "state": read_state(state_path),
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    size = sum(p["bytes"] for p in writer.parts)
    print(
        f"[books] exported {rows:,} books ({embedded:,} embedded) as"
        f" {len(writer.parts)} part(s), {size / 1e9:.2f} GB, in"
        f" {time.monotonic() - started:.0f}s",
        flush=True,
    )
    return manifest


# ---------------------------------------------------------------------
# Load


def find_release_manifest(repo: str = DEFAULT_REPO) -> str | None:
    """The manifest URL of the newest books release, or None.

    Books releases are never marked latest - that marker belongs to the
    anime dataset, whose stable download URL follows it - so the newest
    is found by tag prefix instead.
    """
    headers = {"Accept": "application/vnd.github+json"}
    # Optional. It only lifts the API rate limit, which unauthenticated
    # callers share by IP - a CI runner's IP is shared with thousands of
    # other jobs. A NAS loading the public release needs none.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 100},
        headers=headers,
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    releases = [r for r in resp.json() if str(r.get("tag_name", "")).startswith(RELEASE_TAG_PREFIX)]
    releases.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    for release in releases:
        for asset in release.get("assets") or []:
            if asset.get("name") == MANIFEST_NAME:
                return asset["browser_download_url"]
    return None


def read_manifest(location: str | Path) -> tuple[dict, str | Path]:
    """The manifest, and the base its parts are found relative to."""
    if isinstance(location, Path):
        return json.loads(location.read_text()), location.parent
    resp = httpx.get(location, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.json(), location.rsplit("/", 1)[0]


def iter_part(base: str | Path, name: str):
    if isinstance(base, Path):
        with (base / name).open("rb") as fh:
            while chunk := fh.read(1 << 20):
                yield chunk
        return
    with httpx.stream("GET", f"{base}/{name}", timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        yield from resp.iter_bytes(1 << 20)


# How often a load reports progress, in compressed bytes read. Each report
# is a row write the settings page polls; every 64 MB is a few dozen of
# them over a whole download.
PROGRESS_EVERY = 64 * 1024 * 1024


def iter_payload(manifest: dict, base: str | Path, progress=None):
    """Decompressed COPY data across every part, each part verified.

    Verified as it streams rather than downloaded first, so a load needs
    no free disk the size of the dataset. A mismatch raises inside the
    load's transaction, which rolls the whole load back - nothing from a
    corrupt or tampered part is ever committed.
    """
    inflate = zlib.decompressobj(wbits=31)
    total = sum(part["bytes"] for part in manifest["parts"])
    done = reported = 0
    for part in manifest["parts"]:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter_part(base, part["name"]):
            digest.update(chunk)
            size += len(chunk)
            done += len(chunk)
            if progress is not None and done - reported >= PROGRESS_EVERY:
                reported = done
                progress({"phase": "downloading", "bytes": done, "total": total})
            out = inflate.decompress(chunk)
            if out:
                yield out
        if size != part["bytes"] or digest.hexdigest() != part["sha256"]:
            raise ValueError(
                f"{part['name']} does not match its manifest"
                f" ({size:,} bytes, expected {part['bytes']:,}); nothing was loaded"
            )
        print(f"[books] verified {part['name']} ({size / 1e9:.2f} GB)", flush=True)
    tail = inflate.flush()
    if tail:
        yield tail
    if not inflate.eof:
        raise ValueError("the dataset ends mid-stream; a part is missing")


def vector_index_ddl() -> list[str]:
    """The vector indexes' CREATE statements, read from db/schema.sql.

    Read rather than restated, so a change to the indexes - a new
    dimension, a new parameter - cannot leave a stale copy here building
    the old one.
    """
    from api.db import _schema_path

    path = _schema_path()
    if path is None:
        raise FileNotFoundError("db/schema.sql not found")
    statements = re.findall(
        r"CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_\d+\b.*?;", path.read_text(), re.S
    )
    names = sorted(stmt.split()[5] for stmt in statements)
    if names != sorted(VECTOR_INDEXES):
        raise ValueError(f"schema.sql defines vector indexes {names}, expected {VECTOR_INDEXES}")
    return statements


def load(
    location: str | Path,
    state_path: Path | None = None,
    from_release: bool = False,
    ensure: bool = True,
    rebuild_indexes: bool = False,
    progress=None,
) -> dict:
    """Merge a books dataset into this database. See the module docstring.

    progress, if given, is called with a dict of phase and byte counts as
    the load goes, for the settings page; it must not raise.
    """
    import psycopg

    from api.config import settings

    manifest, base = read_manifest(location)
    if manifest.get("format") != FORMAT:
        raise ValueError(f"unknown dataset format {manifest.get('format')!r}; expected {FORMAT}")
    if ensure:
        from api.db import ensure_schema

        ensure_schema()

    model = current_model_id()
    started = time.monotonic()
    with psycopg.connect(settings.database_url) as conn:
        if rebuild_indexes:
            # A bulk load into an empty-ish catalogue builds the HNSW graph
            # faster once, afterwards, than one insert at a time - pgvector's
            # own guidance. Inside the transaction, so a failed load puts the
            # indexes back with everything else.
            for name in VECTOR_INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.execute("DROP TABLE IF EXISTS book_stage")
        conn.execute(STAGE_SQL)
        target = [list(r) for r in conn.execute(STAGE_TYPES_SQL).fetchall()]
        if target != manifest["columns"]:
            raise ValueError(
                "this database's columns do not match the dataset's; refusing to load."
                f" dataset {manifest['columns']} vs here {target}"
            )
        with conn.cursor() as cur, cur.copy("COPY book_stage FROM STDIN (FORMAT binary)") as copy:
            for data in iter_payload(manifest, base, progress):
                copy.write(data)
        if progress is not None:
            progress({"phase": "merging"})
        staged = conn.execute("SELECT count(*) FROM book_stage").fetchone()[0]
        updated = conn.execute(UPDATE_SQL).rowcount
        inserted = conn.execute(INSERT_SQL).rowcount
        if manifest["embed_model"] == model:
            vectors = conn.execute(EMBEDDINGS_SQL, {"model": model}).rowcount
        else:
            # A different model's vectors live in a different space, and
            # searching across the two would rank nonsense. The rows are
            # still worth having: the nightly pass embeds them with this
            # database's model.
            vectors = 0
            print(
                f"[books] dataset embeddings are {manifest['embed_model']}, this database"
                f" uses {model}; loading the books without them",
                flush=True,
            )
        conn.execute("DROP TABLE book_stage")
        conn.commit()

    if rebuild_indexes:
        # The two statements alone, not ensure_schema(): that holds an
        # advisory lock the API also takes at startup, and an API restart
        # during an hour-long index build would hang behind it. If this
        # process dies before the build finishes, the next startup's
        # ensure_schema() creates the indexes anyway.
        built = time.monotonic()
        if progress is not None:
            progress({"phase": "indexing"})
        print("[books] building the vector indexes over the loaded catalogue", flush=True)
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            for ddl in vector_index_ddl():
                conn.execute(ddl)
        print(f"[books] vector indexes built in {time.monotonic() - built:.0f}s", flush=True)

    state = dict(manifest.get("state") or {})
    if from_release:
        # What the newest release already carries, so a build that
        # changes nothing does not publish a copy of it.
        state["published_dump_version"] = state.get("dump_version")
    write_state(state_path, state)
    print(
        f"[books] loaded {staged:,} books ({inserted:,} new, {updated:,} updated,"
        f" {vectors:,} embeddings) in {time.monotonic() - started:.0f}s",
        flush=True,
    )
    return {"staged": staged, "inserted": inserted, "updated": updated, "embeddings": vectors}


# ---------------------------------------------------------------------
# Seeding a fresh install is an ordinary install request now
# (pipeline.dataset_install), made by the worker when it starts against an
# empty catalogue. The settings page makes the same request when a reader
# switches Books on.


# ---------------------------------------------------------------------
# The CI build's own steps


def prepare_ci() -> None:
    """Schema, then drop the vector indexes. CI only: it never searches."""
    import psycopg

    from api.config import settings
    from api.db import ensure_schema

    ensure_schema()
    with psycopg.connect(settings.database_url) as conn:
        for name in VECTOR_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()
    print(f"[books] schema ready; dropped {', '.join(VECTOR_INDEXES)} for the build", flush=True)


def run_import(state_path: Path) -> bool:
    """Import the current dump unless this build already has. True if complete."""
    from pipeline.openlibrary import MIN_DESCRIPTION, dump_version
    from pipeline.sync_openlibrary import sync

    state = read_state(state_path)
    version = dump_version()
    if state.get("import_complete") and state.get("dump_version") == version:
        print(f"[books] {version} already imported; skipping the dump", flush=True)
        return True
    status: dict = {}
    # No cache: a runner is gone after the job, and its /root is not
    # writable anyway.
    sync(None, MIN_DESCRIPTION, 1000, cache=None, status=status)
    complete = not status.get("truncated")
    state.update({"dump_version": version, "import_complete": complete})
    write_state(state_path, state)
    return complete


def pending() -> int:
    import psycopg

    from api.config import settings

    with psycopg.connect(settings.database_url) as conn:
        return conn.execute(PENDING_SQL, {"model": current_model_id()}).fetchone()[0]


def should_publish(state: dict, left: int) -> bool:
    """Complete, fully embedded, and not what the last release already holds."""
    return (
        bool(state.get("import_complete"))
        and left == 0
        and state.get("dump_version") != state.get("published_dump_version")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or load the books dataset")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="write the books to a manifest and parts")
    p.add_argument("--out", type=Path, default=Path("book-dataset"))
    p.add_argument("--state", type=Path, default=None)

    p = sub.add_parser("load", help="merge a books dataset into this database")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--dir", type=Path)
    where.add_argument("--manifest-url")
    where.add_argument("--release", choices=["latest"])
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--state", type=Path, default=None)
    p.add_argument("--no-schema", action="store_true", help="skip ensure_schema (CI)")

    sub.add_parser("prepare-ci", help="schema plus no vector indexes, for a build")

    p = sub.add_parser("import", help="import the current dump unless already done")
    p.add_argument("--state", type=Path, required=True)

    sub.add_parser("pending", help="books still lacking a current embedding")

    p = sub.add_parser("status", help="key=value lines for $GITHUB_OUTPUT")
    p.add_argument("--state", type=Path, required=True)

    args = parser.parse_args()
    if args.cmd == "export":
        export(args.out, args.state)
    elif args.cmd == "load":
        if args.release:
            url = find_release_manifest(args.repo)
            if url is None:
                print("[books] no books release published yet; nothing to load")
                return
            load(url, args.state, from_release=True, ensure=not args.no_schema)
        elif args.dir:
            load(args.dir / MANIFEST_NAME, args.state, ensure=not args.no_schema)
        else:
            load(args.manifest_url, args.state, ensure=not args.no_schema)
    elif args.cmd == "prepare-ci":
        prepare_ci()
    elif args.cmd == "import":
        if not run_import(args.state):
            sys.exit("[books] the dump stream ended early; the import is incomplete")
    elif args.cmd == "pending":
        print(pending())
    elif args.cmd == "status":
        state = read_state(args.state)
        left = pending()
        print(f"pending={left}")
        print(f"import_complete={str(bool(state.get('import_complete'))).lower()}")
        print(f"publish={str(should_publish(state, left)).lower()}")


if __name__ == "__main__":
    main()
