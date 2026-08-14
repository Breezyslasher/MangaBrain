"""Dump the catalog data tables to a portable snapshot (pg_dump custom
format). The snapshot contains media, relations, embeddings, and the sync
checkpoints, so a restored instance resumes incremental syncing from the
snapshot's timestamp instead of starting over. User-specific tables (tracker
list caches, exclusion lists, account settings, backfill state) are excluded
by default and MUST stay out of DATA_TABLES: the public weekly dataset is
built without --all, and app_settings holds the Yamtrack token. Pass --all
to include them for personal backups.

Usage:
    python -m pipeline.snapshot --out mangabrain-dataset.dump
    python -m pipeline.snapshot --all --out backup.dump   # full personal backup
"""

import argparse
import subprocess
from pathlib import Path

from api.config import settings

DATA_TABLES = ("media", "media_relations", "embeddings", "sync_state")
USER_TABLES = (
    "mal_list_entries",
    "mal_list_state",
    "anilist_list_entries",
    "anilist_list_state",
    "jikan_backfill_state",
    "custom_exclusion_entries",
    "custom_exclusion_state",
    "app_settings",
)


def create_snapshot(out_path: str, include_user_tables: bool = False) -> None:
    cmd = [
        "pg_dump",
        "--dbname",
        settings.database_url,
        "--format",
        "custom",
        "--no-owner",
        "--no-privileges",
    ]
    tables = DATA_TABLES + (USER_TABLES if include_user_tables else ())
    for table in tables:
        cmd += ["--table", table]
    cmd += ["--file", out_path]
    subprocess.run(cmd, check=True)
    size_mb = Path(out_path).stat().st_size / (1024 * 1024)
    print(f"[snapshot] wrote {out_path} ({size_mb:.0f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump catalog tables to a snapshot")
    parser.add_argument("--out", default="mangabrain-dataset.dump")
    parser.add_argument(
        "--all",
        action="store_true",
        help="include user tables (tracker lists, exclusion lists, settings,"
        " backfill state) for personal backups",
    )
    args = parser.parse_args()
    create_snapshot(args.out, include_user_tables=args.all)


if __name__ == "__main__":
    main()
