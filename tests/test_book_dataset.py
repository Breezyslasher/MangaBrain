"""The books dataset, the parts of it that need no database.

The round trip through a real Postgres is tests/test_book_dataset_pg.py.
These cover the split, the publish decision, and the workflow guards -
two of which exist only to protect the anime dataset from this one.
"""

import gzip
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from pipeline import book_dataset as bd

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/books-dataset.yml"


def test_parts_split_at_the_cap_and_reassemble_exactly(tmp_path):
    payload = bytes(range(256)) * 400  # 102,400 bytes
    writer = bd.PartWriter(tmp_path, part_bytes=10_000)
    with gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=0, mtime=0) as gz:
        gz.write(payload)
    writer.close()

    assert len(writer.parts) > 1
    assert all(p["bytes"] == 10_000 for p in writer.parts[:-1]), "every part but the last is full"
    for part in writer.parts:
        data = (tmp_path / part["name"]).read_bytes()
        assert len(data) == part["bytes"]
        assert hashlib.sha256(data).hexdigest() == part["sha256"]

    manifest = {"parts": writer.parts}
    assert b"".join(bd.iter_payload(manifest, tmp_path)) == payload


def test_a_part_that_does_not_match_its_manifest_is_refused(tmp_path):
    writer = bd.PartWriter(tmp_path, part_bytes=10**6)
    with gzip.GzipFile(fileobj=writer, mode="wb", mtime=0) as gz:
        gz.write(b"x" * 1000)
    writer.close()
    writer.parts[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not match its manifest"):
        b"".join(bd.iter_payload({"parts": writer.parts}, tmp_path))


def test_parts_stay_under_github_s_asset_cap():
    assert bd.PART_BYTES < 2 * 1024**3


@pytest.mark.parametrize(
    ("state", "left", "expected"),
    [
        ({"import_complete": True, "dump_version": "B"}, 0, True),
        ({"import_complete": True, "dump_version": "B", "published_dump_version": "A"}, 0, True),
        # Nothing new since the last release: do not publish a copy of it.
        ({"import_complete": True, "dump_version": "B", "published_dump_version": "B"}, 0, False),
        # Books still unembedded: not a dataset yet.
        ({"import_complete": True, "dump_version": "B"}, 5, False),
        # A truncated import is never published as a whole one.
        ({"import_complete": False, "dump_version": "B"}, 0, False),
        ({}, 0, False),
    ],
)
def test_should_publish(state, left, expected):
    assert bd.should_publish(state, left) is expected


def test_the_dataset_carries_exactly_what_the_importer_writes():
    """If sync_openlibrary starts writing a column the dataset does not
    carry, a load silently leaves it NULL. Pinned against the importer's
    own SQL so the two cannot drift."""
    from pipeline.sync_openlibrary import UPSERT_SQL

    written = re.search(r"INSERT INTO media \((.*?)\)", UPSERT_SQL, re.S).group(1)
    columns = [c.strip() for c in written.split(",") if c.strip() != "id"]
    assert tuple(columns) == bd.BOOK_COLUMNS


def test_a_load_never_rolls_a_newer_row_back():
    assert "m.synced_at < s.synced_at" in bd.UPDATE_SQL


def test_a_load_keeps_vectors_the_database_computed_itself():
    assert "DO NOTHING" in bd.EMBEDDINGS_SQL


def test_a_load_only_draws_ids_for_new_books():
    """Upsert-with-nextval spends an id per row, inserted or not, on a
    32-bit column. The insert here is restricted to rows not present."""
    assert "nextval" in bd.INSERT_SQL and "WHERE NOT EXISTS" in bd.INSERT_SQL
    assert "nextval" not in bd.UPDATE_SQL


def test_a_load_touches_open_library_rows_only():
    assert "m.source = s.source AND m.source_id = s.source_id" in bd.UPDATE_SQL
    assert "m.source = 'openlibrary'" in bd.PENDING_SQL


def test_the_newest_books_release_is_found_by_tag_not_latest(monkeypatch):
    """Books releases are never marked latest, so they are found by
    prefix - and an anime release, even a newer one, is never taken."""

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            asset = {"name": bd.MANIFEST_NAME, "browser_download_url": "{}"}
            return [
                {"tag_name": "dataset-20261109", "created_at": "2026-11-09", "assets": []},
                {
                    "tag_name": "books-20261003",
                    "created_at": "2026-10-03",
                    "assets": [dict(asset, browser_download_url="old")],
                },
                {
                    "tag_name": "books-20261103",
                    "created_at": "2026-11-03",
                    "assets": [dict(asset, browser_download_url="new")],
                },
            ]

    monkeypatch.setattr(bd.httpx, "get", lambda *a, **k: Resp())
    assert bd.find_release_manifest() == "new"


# ---------------------------------------------------------------------
# The workflow


@pytest.fixture(scope="module")
def flow():
    spec = yaml.safe_load(WORKFLOW.read_text())
    steps = {s.get("id") or s["name"]: s for s in spec["jobs"]["build"]["steps"]}
    return spec, steps


def test_a_books_release_never_takes_the_latest_marker(flow):
    """Every new install seeds from releases/latest/download/
    mangabrain-dataset.dump. GitHub marks a new release latest by
    default, so a books release without this would make that URL 404."""
    _, steps = flow
    run = steps["Publish release"]["run"]
    assert "gh release create" in run and "--latest=false" in run
    assert f'TAG="{bd.RELEASE_TAG_PREFIX}' in run


def test_neither_workflow_prunes_the_other_s_releases(flow):
    _, steps = flow
    assert 'startswith("books-")' in steps["Prune old books releases"]["run"]
    anime = (ROOT / ".github/workflows/dataset-publish.yml").read_text()
    assert 'startswith("dataset-")' in anime
    assert not bd.RELEASE_TAG_PREFIX.startswith("dataset-")


def test_the_checkpoints_do_not_share_a_cache_prefix(flow):
    _, steps = flow
    assert steps["checkpoint"]["with"]["restore-keys"].strip() == "books-checkpoint-"
    assert "dataset-checkpoint-" not in WORKFLOW.read_text()


def test_the_export_is_skipped_when_the_state_never_arrived(flow):
    """Otherwise a failed load exports a near-empty database as the
    newest checkpoint, and the next step deletes the real one."""
    _, steps = flow
    cond = steps["export"]["if"]
    for guard in (
        "steps.prepare.outcome == 'success'",
        "steps.load.outcome != 'failure'",
        "steps.seed.outcome != 'failure'",
    ):
        assert guard in cond, guard


def test_the_old_checkpoint_is_deleted_only_after_the_new_one_saved(flow):
    _, steps = flow
    cond = steps["Delete the checkpoint this job started from"]["if"]
    assert "steps.save.outcome == 'success'" in cond


def test_a_resume_is_dispatched_only_for_a_budgeted_stop(flow):
    """Not on a crash, not on an import failure, not on an unreadable
    status - each would resume straight into the same failure."""
    _, steps = flow
    step = steps["Dispatch resume"]
    cond = step["if"]
    for guard in (
        "steps.save.outcome == 'success'",
        "steps.embed.outcome == 'success'",
        "steps.status.outcome == 'success'",
        "steps.status.outputs.pending != ''",
        "steps.status.outputs.pending != '0'",
    ):
        assert guard in cond, guard
    assert "--ref" in step["run"]
    assert re.search(r"-ge \d+", step["run"]), "the chain is capped"


def test_the_embed_step_stops_on_a_budget_rather_than_a_kill(flow):
    _, steps = flow
    assert "pipeline.embed --max-seconds" in steps["embed"]["run"]


def test_the_status_step_cannot_hide_a_crash_behind_its_tee(flow):
    _, steps = flow
    assert "set -o pipefail" in steps["status"]["run"]


def test_the_ci_tests_have_a_real_database():
    spec = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
    job = spec["jobs"]["test"]
    assert job["services"]["db"]["image"].startswith("pgvector/pgvector")
    assert job["env"]["MANGABRAIN_TEST_PG"].startswith("postgresql://")
