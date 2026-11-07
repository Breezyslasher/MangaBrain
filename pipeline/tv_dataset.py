"""The TV dataset: TVmaze's shows with their embeddings, as a release.

Fetching TVmaze takes minutes; embedding ~48,000 shows takes hours on a
NAS. So a release carries the embeddings, and an install that loads one
only has to top up what changed since it was built.

Only TVmaze rows, never TMDB's. TVmaze's data is CC BY-SA, which allows
republishing it - and requires the release to be CC BY-SA too, credited
to TVmaze, which the workflow's release notes and LICENSE file do. TMDB's
terms forbid redistribution, so the TV shows TMDB adds on an install
(pipeline.sync_tmdb) stay on that install and are never exported.

Same on-disk format as the books dataset (pipeline.book_dataset): a
binary COPY over a query, gzipped, cut into parts under GitHub's asset
cap, with a manifest naming every part and its SHA-256 and recording the
column types. A load merges on (source, source_id): newer rows update,
new rows insert, embeddings this database computed itself are kept, and
nothing outside TVmaze's rows is touched.

    python -m pipeline.tv_dataset build --out tv-dataset   # CI: fetch, embed, export
    python -m pipeline.tv_dataset load --release latest
    python -m pipeline.tv_dataset load --dir tv-dataset
"""

import argparse
import gzip
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from pipeline.book_dataset import (
    DEFAULT_REPO,
    PART_BYTES,
    PartWriter,
    current_model_id,
    find_release_manifest,
    iter_payload,
    read_manifest,
)

FORMAT = "mangabrain-tv/1"
MANIFEST_NAME = "mangabrain-tv.manifest.json"
PART_PREFIX = "mangabrain-tv.copy.gz.part"
RELEASE_TAG_PREFIX = "tv-"
LOADED_RELEASE_KEY = "tv_loaded_release"
SOURCE = "tvmaze"

# Exactly what pipeline.sync_tvmaze writes, plus its identity.
TV_COLUMNS = (
    "source",
    "source_id",
    "media_type",
    "medium",
    "title_romaji",
    "title_english",
    "description",
    "description_clean",
    "genres",
    "tags",
    "format",
    "country_of_origin",
    "start_year",
    "status",
    "cover_image_medium",
    "cover_image_large",
    "external_ids",
    "synced_at",
)
# Tags are not updated by a load: TVmaze has none, so on an install they
# hold TMDB's keywords (pipeline.sync_tmdb tvmaze-keywords), and a release's
# empty tags must not wipe them. A new show is inserted with the release's.
UPDATE_COLUMNS = tuple(c for c in TV_COLUMNS if c not in ("source", "source_id", "tags"))


def select_sql(model_literal: str, where: str) -> str:
    cols = ", ".join(f"m.{c}" for c in TV_COLUMNS)
    return (
        f"SELECT {cols}, e.embedding FROM media m"
        f" LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = {model_literal}"
        f" WHERE {where}"
    )


STAGE_SQL = "CREATE TEMP TABLE tv_stage AS " + select_sql("''", "false")

STAGE_TYPES_SQL = """
    SELECT a.attname, format_type(a.atttypid, a.atttypmod)
    FROM pg_attribute a
    WHERE a.attrelid = 'tv_stage'::regclass AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
"""

# The source filter is the licence boundary: TVmaze's rows only.
EXPORT_WHERE = "m.source = 'tvmaze' ORDER BY m.source_id"

# Only where the release is newer, so a load never rolls back a show this
# database re-read from TVmaze after the release was built.
UPDATE_SQL = (
    "UPDATE media m SET "
    + ", ".join(f"{c} = s.{c}" for c in UPDATE_COLUMNS)
    + " FROM tv_stage s WHERE m.source = s.source AND m.source_id = s.source_id"
    " AND (m.synced_at IS NULL OR m.synced_at < s.synced_at)"
)

INSERT_SQL = (
    "INSERT INTO media (id, "
    + ", ".join(TV_COLUMNS)
    + ") SELECT nextval('media_external_id_seq'), "
    + ", ".join(f"s.{c}" for c in TV_COLUMNS)
    + " FROM tv_stage s WHERE s.source = 'tvmaze' AND NOT EXISTS ("
    "SELECT 1 FROM media m WHERE m.source = s.source AND m.source_id = s.source_id)"
)

EMBEDDINGS_SQL = """
    INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)
    SELECT m.id, %(model)s, s.embedding, now()
    FROM tv_stage s
    JOIN media m ON m.source = s.source AND m.source_id = s.source_id
    WHERE s.embedding IS NOT NULL
    ON CONFLICT (media_id, embed_model) DO NOTHING
"""

# TMDB's keywords on TVmaze rows: TMDB's terms forbid redistributing them,
# and a CC BY-SA release must hold only what TVmaze provides.
TMDB_TAGS_SQL = "SELECT count(*) FROM media WHERE source = 'tvmaze' AND tags <> '[]'::jsonb"

COUNTS_SQL = """
    SELECT count(*), count(e.media_id) FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.source = 'tvmaze'
"""


def column_types(conn) -> list[list[str]]:
    conn.execute("DROP TABLE IF EXISTS tv_stage")
    conn.execute(STAGE_SQL)
    types = [list(r) for r in conn.execute(STAGE_TYPES_SQL).fetchall()]
    conn.execute("DROP TABLE tv_stage")
    return types


def export(out_dir: Path) -> dict:
    import psycopg
    from psycopg import sql

    from api.config import settings

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{PART_PREFIX}*"):
        stale.unlink()
    model = current_model_id()
    with psycopg.connect(settings.database_url) as conn:
        tagged = conn.execute(TMDB_TAGS_SQL).fetchone()[0]
        if tagged:
            # Their embeddings were computed with TMDB's keywords too, so
            # blanking the tags would not be enough.
            raise RuntimeError(
                f"{tagged:,} TVmaze shows carry TMDB keywords on this database; a TV"
                " release must be built from TVmaze alone (TMDB forbids redistribution)."
                " Build it in CI, where no TMDB key is set."
            )
        types = column_types(conn)
        rows, embedded = conn.execute(COUNTS_SQL, {"model": model}).fetchone()
        query = (
            "COPY ("
            + select_sql(sql.Literal(model).as_string(conn), EXPORT_WHERE)
            + ") TO STDOUT (FORMAT binary)"
        )
        writer = PartWriter(out_dir, PART_BYTES, prefix=PART_PREFIX)
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
        "licence": "CC BY-SA 4.0; TV data from TVmaze (https://www.tvmaze.com)",
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    print(f"[tv] exported {rows:,} shows ({embedded:,} embedded)", flush=True)
    return manifest


def find_release(repo: str = DEFAULT_REPO) -> str | None:
    return find_release_manifest(repo, RELEASE_TAG_PREFIX, MANIFEST_NAME)


def load(location: str | Path, progress=None) -> dict:
    """Merge a TV release into this database. See the module docstring."""
    import psycopg

    from api.config import settings
    from pipeline.sync_anilist import set_state

    manifest, base = read_manifest(location)
    if manifest.get("format") != FORMAT:
        raise ValueError(f"unknown dataset format {manifest.get('format')!r}; expected {FORMAT}")
    model = current_model_id()
    started = time.monotonic()
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DROP TABLE IF EXISTS tv_stage")
        conn.execute(STAGE_SQL)
        target = [list(r) for r in conn.execute(STAGE_TYPES_SQL).fetchall()]
        if target != manifest["columns"]:
            raise ValueError(
                "this database's columns do not match the dataset's; refusing to load."
                f" dataset {manifest['columns']} vs here {target}"
            )
        with conn.cursor() as cur, cur.copy("COPY tv_stage FROM STDIN (FORMAT binary)") as copy:
            for data in iter_payload(manifest, base, progress):
                copy.write(data)
        if progress is not None:
            progress({"phase": "merging"})
        staged = conn.execute("SELECT count(*) FROM tv_stage").fetchone()[0]
        updated = conn.execute(UPDATE_SQL).rowcount
        inserted = conn.execute(INSERT_SQL).rowcount
        vectors = 0
        if manifest["embed_model"] == model:
            vectors = conn.execute(EMBEDDINGS_SQL, {"model": model}).rowcount
        else:
            print(
                f"[tv] release embeddings are {manifest['embed_model']}, this database uses"
                f" {model}; loading the shows without them",
                flush=True,
            )
        conn.execute("DROP TABLE tv_stage")
        if isinstance(location, str):
            set_state(
                conn,
                LOADED_RELEASE_KEY,
                {
                    "manifest": location,
                    "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
        conn.commit()
    print(
        f"[tv] loaded {staged:,} shows ({inserted:,} new, {updated:,} updated,"
        f" {vectors:,} embeddings) in {time.monotonic() - started:.0f}s",
        flush=True,
    )
    return {"staged": staged, "inserted": inserted, "updated": updated, "embeddings": vectors}


def build(out_dir: Path) -> dict:
    """The CI build: schema, a full TVmaze read, embeddings, export."""
    from api.db import ensure_schema
    from pipeline.embed import embed_missing
    from pipeline.sync_tvmaze import sync

    ensure_schema()
    tally = sync()
    if tally["kept"] < 10_000:
        raise RuntimeError(f"only {tally['kept']:,} shows kept; refusing to publish a thin dataset")
    embed_missing(sources=(SOURCE,))
    return export(out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build, export or load the TV dataset")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="fetch TVmaze, embed, export (CI)")
    b.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("load")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--release", choices=["latest"])
    where.add_argument("--dir", type=Path)
    p.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args()
    if args.cmd == "build":
        build(args.out)
    elif args.cmd == "export":
        export(args.out)
    elif args.dir:
        load(args.dir / MANIFEST_NAME)
    else:
        url = find_release_manifest(args.repo, RELEASE_TAG_PREFIX, MANIFEST_NAME)
        if url is None:
            raise SystemExit("[tv] no TV release published yet; nothing to load")
        load(url)


if __name__ == "__main__":
    main()
