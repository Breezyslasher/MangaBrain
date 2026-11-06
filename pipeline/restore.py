"""Seed the database from a published catalog snapshot.

Downloads (or reads) a pg_dump custom-format snapshot produced by
pipeline.snapshot and restores the catalog tables. Because sync_state is part
of the snapshot, the nightly incremental sync picks up right where the
snapshot left off, so a fresh install is browsable in minutes instead of
hours.

Usage:
    python -m pipeline.restore --url https://github.com/OWNER/REPO/releases/latest/download/mangabrain-dataset.dump
    python -m pipeline.restore --file mangabrain-dataset.dump
    python -m pipeline.restore --url ... --force   # replace an existing catalog
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import httpx
import psycopg

from api.config import settings
from api.db import ensure_schema

# The tables the snapshot carries. Anything else in the database is
# local - user lists, backfill state - and must survive a restore.
SNAPSHOT_TABLES = ["media", "media_relations", "embeddings", "sync_state"]

DEPENDENT_FKS_SQL = """
    SELECT conrelid::regclass::text AS child, conname
    FROM pg_constraint
    WHERE contype = 'f'
      AND confrelid::regclass::text = ANY(%(tables)s)
      AND conrelid::regclass::text <> ALL(%(tables)s)
"""


def catalog_is_empty() -> bool:
    """True for an empty catalogue, and for a database with no schema yet.

    A missing media table is the emptiest a catalogue can be. Treating
    it as an error forced ensure_schema() to run first, which is what
    made the restore fail.
    """
    with psycopg.connect(settings.database_url) as conn:
        try:
            row = conn.execute("SELECT 1 FROM media LIMIT 1").fetchone()
        except psycopg.errors.UndefinedTable:
            return True
    return row is None


# The snapshot's own indexes need these before pg_restore runs: its
# trigram indexes reference gin_trgm_ops and its embedding column is a
# vector. Moving ensure_schema() after the restore took the extensions
# with it, and the restore failed on "operator class public.gin_trgm_ops
# does not exist" - a fix that traded one failure for another.
#
# Extensions only. Creating the TABLES before a restore is what the
# reordering exists to avoid.
EXTENSIONS = ("vector", "pg_trgm")


def ensure_extensions() -> None:
    with psycopg.connect(settings.database_url) as conn:
        for extension in EXTENSIONS:
            conn.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        conn.commit()
    print(f"[restore] extensions ready: {', '.join(EXTENSIONS)}")


def drop_dependent_constraints() -> int:
    """Let pg_restore --clean drop the snapshot's tables.

    Measured: the model-bench job failed with "cannot drop constraint
    media_pkey ... dub_backfill_state_media_id_fkey depends on index
    public.media_pkey". pg_restore has no CASCADE, so one local table
    holding a foreign key into media stopped the whole restore - the
    drop failed, CREATE TABLE then failed as "already exists", and the
    primary key as "multiple primary keys".

    The constraints come back when ensure_schema() runs afterwards.
    """
    dropped = 0
    with psycopg.connect(settings.database_url) as conn:
        try:
            rows = conn.execute(DEPENDENT_FKS_SQL, {"tables": SNAPSHOT_TABLES}).fetchall()
        except psycopg.errors.UndefinedTable:
            return 0
        for child, conname in rows:
            conn.execute(f'ALTER TABLE {child} DROP CONSTRAINT IF EXISTS "{conname}"')
            dropped += 1
        conn.commit()
    if dropped:
        print(f"[restore] dropped {dropped} foreign key(s) into the snapshot's tables")
    return dropped


def download(url: str, dest: Path) -> None:
    print(f"[restore] downloading {url}")
    # GitHub release assets redirect to object storage, so follow redirects.
    with (
        httpx.Client(follow_redirects=True, timeout=60) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes(1024 * 1024):
                fh.write(chunk)
    print(f"[restore] downloaded {dest.stat().st_size / (1024 * 1024):.0f} MB")


def restore_file(path: Path) -> None:
    # --clean drops and recreates the snapshot's tables (indexes included),
    # so the restore is a clean replacement regardless of prior state.
    subprocess.run(
        [
            "pg_restore",
            "--dbname",
            settings.database_url,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            str(path),
        ],
        check=True,
    )
    print("[restore] catalog restored")


def seed_if_empty(url: str) -> bool:
    """Restore from url when the catalog is empty. Returns True if seeded."""
    if not catalog_is_empty():
        ensure_schema()
        print("[restore] catalog already populated; skipping snapshot seed")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "snapshot.dump"
        download(url, dest)
        ensure_extensions()
        drop_dependent_constraints()
        restore_file(dest)
    # After, not before: the schema's own tables would otherwise hold
    # foreign keys into the tables pg_restore needs to drop, and its
    # migrations belong on top of the restored snapshot anyway.
    ensure_schema()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore the catalog from a snapshot")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--file")
    parser.add_argument(
        "--force", action="store_true", help="restore even if the catalog already has data"
    )
    args = parser.parse_args()

    if not args.force and not catalog_is_empty():
        ensure_schema()
        parser.exit(
            message="catalog already populated; pass --force to replace it from the snapshot\n"
        )
    ensure_extensions()
    drop_dependent_constraints()
    if args.file:
        restore_file(Path(args.file))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "snapshot.dump"
            download(args.url, dest)
            restore_file(dest)
    # After the restore: ensure_schema's tables hold foreign keys into
    # the snapshot's, which is exactly what stopped pg_restore --clean
    # from dropping them.
    ensure_schema()


if __name__ == "__main__":
    main()
