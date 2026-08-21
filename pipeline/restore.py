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


def catalog_is_empty() -> bool:
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute("SELECT 1 FROM media LIMIT 1").fetchone()
    return row is None


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
    ensure_schema()
    if not catalog_is_empty():
        print("[restore] catalog already populated; skipping snapshot seed")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "snapshot.dump"
        download(url, dest)
        restore_file(dest)
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

    ensure_schema()
    if not args.force and not catalog_is_empty():
        parser.exit(
            message="catalog already populated; pass --force to replace it from the snapshot\n"
        )
    if args.file:
        restore_file(Path(args.file))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "snapshot.dump"
            download(args.url, dest)
            restore_file(dest)


if __name__ == "__main__":
    main()
