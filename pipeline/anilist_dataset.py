"""The anime and manga catalogue as a dataset that MERGES on load.

The weekly anime release has always been a pg_dump snapshot
(mangabrain-dataset.dump), restored with pg_restore --clean. That replaces
whole tables, so it can only seed an empty install: over a live one it
would delete every book, TV show and film. It stays as it is, for
seeding. Beside it the release now also carries this: the AniList rows,
their relations and their embeddings, in the books dataset's format (a
gzipped binary COPY in parts, with a manifest of SHA-256s and column
types), so an install that already has other media can reload anime and
manga from it.

A load:
  - touches only rows with source 'anilist', and their relations;
  - updates a title only where the release's AniList updatedAt is newer
    than this database's, and replaces that title's relations with the
    release's;
  - inserts titles this database does not have, under their AniList id;
  - adds embeddings this database lacks and keeps the ones it computed.

Columns are carried by name. The release's columns must exist here with
the same types - a database older than the release refuses to load and
says why - but a newer database with extra columns loads fine, and its
extra columns are left as they are.

    python -m pipeline.anilist_dataset export --out anilist-dataset   # CI
    python -m pipeline.anilist_dataset load --release latest
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

FORMAT = "mangabrain-anilist/1"
MANIFEST_NAME = "mangabrain-anilist.manifest.json"
PART_PREFIX = "mangabrain-anilist.copy.gz.part"
# The anime release's own tag prefix: these files ride in the same
# release as mangabrain-dataset.dump.
RELEASE_TAG_PREFIX = "dataset-"
LOADED_RELEASE_KEY = "anilist_loaded_release"
SOURCE = "anilist"

# Every media column except these is carried. id is carried separately
# (it is the AniList id); source and source_id are fixed for these rows.
SKIP = {"id"}

RELATIONS_SQL = (
    "(SELECT coalesce(jsonb_agg(jsonb_build_array(r.related_id, r.relation_type)), '[]')"
    " FROM media_relations r WHERE r.media_id = m.id)"
)

COLUMNS_SQL = """
    SELECT column_name FROM information_schema.columns
    WHERE table_schema = current_schema() AND table_name = 'media'
    ORDER BY ordinal_position
"""


def media_columns(conn) -> list[str]:
    return [r[0] for r in conn.execute(COLUMNS_SQL).fetchall() if r[0] not in SKIP]


def select_sql(columns: list[str], model_literal: str, where: str) -> str:
    cols = ", ".join(f"m.{c}" for c in columns)
    return (
        f"SELECT m.id, {cols}, {RELATIONS_SQL} AS relations, e.embedding FROM media m"
        f" LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = {model_literal}"
        f" WHERE {where}"
    )


STAGE_TYPES_SQL = """
    SELECT a.attname, format_type(a.atttypid, a.atttypmod)
    FROM pg_attribute a
    WHERE a.attrelid = 'anilist_stage'::regclass AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
"""

COUNTS_SQL = """
    SELECT count(*), count(e.media_id) FROM media m
    LEFT JOIN embeddings e ON e.media_id = m.id AND e.embed_model = %(model)s
    WHERE m.source = 'anilist'
"""


def stage(conn, columns: list[str]) -> list[list[str]]:
    """Create the staging table in this database's types; return them."""
    conn.execute("DROP TABLE IF EXISTS anilist_stage")
    conn.execute("CREATE TEMP TABLE anilist_stage AS " + select_sql(columns, "''", "false"))
    return [list(r) for r in conn.execute(STAGE_TYPES_SQL).fetchall()]


def merge_sql(columns: list[str]) -> tuple[str, str, str]:
    """(applied, update, insert) statements for the given columns."""
    # Decided once, before anything changes: the titles the release
    # brings that are new here, or newer than here by AniList updatedAt.
    applied = (
        "CREATE TEMP TABLE anilist_applied AS SELECT s.id FROM anilist_stage s"
        " LEFT JOIN media m ON m.id = s.id"
        " WHERE s.source = 'anilist' AND (m.id IS NULL OR (m.source = 'anilist' AND"
        " coalesce(m.updated_at, 0) < coalesce(s.updated_at, 0)))"
    )
    updatable = [c for c in columns if c not in ("source", "source_id")]
    update = (
        "UPDATE media m SET "
        + ", ".join(f"{c} = s.{c}" for c in updatable)
        + " FROM anilist_stage s JOIN anilist_applied a ON a.id = s.id WHERE m.id = s.id"
    )
    insert = (
        "INSERT INTO media (id, "
        + ", ".join(columns)
        + ") SELECT s.id, "
        + ", ".join(f"s.{c}" for c in columns)
        + " FROM anilist_stage s JOIN anilist_applied a ON a.id = s.id"
        " WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = s.id)"
    )
    return applied, update, insert


RELATIONS_DELETE_SQL = (
    "DELETE FROM media_relations r USING anilist_applied a WHERE r.media_id = a.id"
)
RELATIONS_INSERT_SQL = """
    INSERT INTO media_relations (media_id, related_id, relation_type)
    SELECT s.id, (rel->>0)::int, rel->>1
    FROM anilist_stage s
    JOIN anilist_applied a ON a.id = s.id
    CROSS JOIN LATERAL jsonb_array_elements(s.relations) AS rel
    ON CONFLICT DO NOTHING
"""
EMBEDDINGS_SQL = """
    INSERT INTO embeddings (media_id, embed_model, embedding, updated_at)
    SELECT s.id, %(model)s, s.embedding, now()
    FROM anilist_stage s
    JOIN media m ON m.id = s.id AND m.source = 'anilist'
    WHERE s.embedding IS NOT NULL
    ON CONFLICT (media_id, embed_model) DO NOTHING
"""


def export(out_dir: Path) -> dict:
    import psycopg
    from psycopg import sql

    from api.config import settings

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{PART_PREFIX}*"):
        stale.unlink()
    model = current_model_id()
    with psycopg.connect(settings.database_url) as conn:
        columns = media_columns(conn)
        types = stage(conn, columns)
        conn.execute("DROP TABLE anilist_stage")
        rows, embedded = conn.execute(COUNTS_SQL, {"model": model}).fetchone()
        query = (
            "COPY ("
            + select_sql(
                columns, sql.Literal(model).as_string(conn), "m.source = 'anilist' ORDER BY m.id"
            )
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
        "media_columns": columns,
        "columns": types,
        "compression": "gzip",
        "parts": writer.parts,
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    print(f"[anilist] exported {rows:,} titles ({embedded:,} embedded)", flush=True)
    return manifest


def find_release(repo: str = DEFAULT_REPO) -> str | None:
    return find_release_manifest(repo, RELEASE_TAG_PREFIX, MANIFEST_NAME)


def load(location: str | Path, progress=None) -> dict:
    """Merge an anime release into this database. See the module docstring."""
    import psycopg

    from api.config import settings
    from pipeline.sync_anilist import set_state

    manifest, base = read_manifest(location)
    if manifest.get("format") != FORMAT:
        raise ValueError(f"unknown dataset format {manifest.get('format')!r}; expected {FORMAT}")
    columns = manifest["media_columns"]
    model = current_model_id()
    started = time.monotonic()
    with psycopg.connect(settings.database_url) as conn:
        here = set(media_columns(conn))
        missing = [c for c in columns if c not in here]
        if missing:
            raise ValueError(
                f"this database has no column(s) {missing}; update MangaBrain before loading"
                " this release"
            )
        target = stage(conn, columns)
        if target != manifest["columns"]:
            raise ValueError(
                "this database's column types do not match the release's; refusing to load."
                f" release {manifest['columns']} vs here {target}"
            )
        with (
            conn.cursor() as cur,
            cur.copy("COPY anilist_stage FROM STDIN (FORMAT binary)") as copy,
        ):
            for data in iter_payload(manifest, base, progress):
                copy.write(data)
        if progress is not None:
            progress({"phase": "merging"})
        staged = conn.execute("SELECT count(*) FROM anilist_stage").fetchone()[0]
        applied_sql, update_sql, insert_sql = merge_sql(columns)
        conn.execute("DROP TABLE IF EXISTS anilist_applied")
        conn.execute(applied_sql)
        updated = conn.execute(update_sql).rowcount
        inserted = conn.execute(insert_sql).rowcount
        conn.execute(RELATIONS_DELETE_SQL)
        relations = conn.execute(RELATIONS_INSERT_SQL).rowcount
        vectors = 0
        if manifest["embed_model"] == model:
            vectors = conn.execute(EMBEDDINGS_SQL, {"model": model}).rowcount
        else:
            print(
                f"[anilist] release embeddings are {manifest['embed_model']}, this database"
                f" uses {model}; loading the titles without them",
                flush=True,
            )
        conn.execute("DROP TABLE anilist_applied")
        conn.execute("DROP TABLE anilist_stage")
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
        f"[anilist] loaded {staged:,} titles ({inserted:,} new, {updated:,} updated,"
        f" {relations:,} relations, {vectors:,} embeddings) in {time.monotonic() - started:.0f}s",
        flush=True,
    )
    return {
        "staged": staged,
        "inserted": inserted,
        "updated": updated,
        "relations": relations,
        "embeddings": vectors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or merge-load the anime dataset")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("load")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--release", choices=["latest"])
    where.add_argument("--dir", type=Path)
    p.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args()
    if args.cmd == "export":
        export(args.out)
    elif args.dir:
        load(args.dir / MANIFEST_NAME)
    else:
        url = find_release(args.repo)
        if url is None:
            raise SystemExit("[anilist] no anime release carries the merge format yet")
        load(url)


if __name__ == "__main__":
    main()
