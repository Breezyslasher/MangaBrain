"""Dump the catalog data tables to a portable snapshot (pg_dump custom
format). The snapshot contains media, relations, embeddings, and the sync
checkpoints, so a restored instance resumes incremental syncing from the
snapshot's timestamp instead of starting over. User-specific tables (MAL and
similar list caches) are excluded.

Usage:
    python -m pipeline.snapshot --out mangabrain-dataset.dump
"""

import argparse
import subprocess
from pathlib import Path

from api.config import settings

DATA_TABLES = ("media", "media_relations", "embeddings", "sync_state")


def create_snapshot(out_path: str) -> None:
    cmd = [
        "pg_dump",
        "--dbname",
        settings.database_url,
        "--format",
        "custom",
        "--no-owner",
        "--no-privileges",
    ]
    for table in DATA_TABLES:
        cmd += ["--table", table]
    cmd += ["--file", out_path]
    subprocess.run(cmd, check=True)
    size_mb = Path(out_path).stat().st_size / (1024 * 1024)
    print(f"[snapshot] wrote {out_path} ({size_mb:.0f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump catalog tables to a snapshot")
    parser.add_argument("--out", default="mangabrain-dataset.dump")
    args = parser.parse_args()
    create_snapshot(args.out)


if __name__ == "__main__":
    main()
