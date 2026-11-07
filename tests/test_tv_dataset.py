"""The published TV dataset: TVmaze's rows only, merged on load."""

import inspect
from pathlib import Path

from pipeline import tv_dataset

ROOT = Path(__file__).resolve().parent.parent


def test_only_tvmaze_rows_are_ever_exported():
    """TVmaze's licence allows republishing; TMDB's terms do not, so its
    shows must never leave the install that fetched them."""
    assert tv_dataset.EXPORT_WHERE.startswith("m.source = 'tvmaze'")
    assert "tmdb" not in tv_dataset.EXPORT_WHERE
    assert "s.source = 'tvmaze'" in tv_dataset.INSERT_SQL


def test_a_load_never_rolls_back_or_replaces_local_work():
    assert "m.synced_at < s.synced_at" in tv_dataset.UPDATE_SQL
    assert "ON CONFLICT (media_id, embed_model) DO NOTHING" in tv_dataset.EMBEDDINGS_SQL


def test_the_columns_are_what_the_tvmaze_sync_writes():
    from pipeline import sync_tvmaze

    for column in ("external_ids", "format", "country_of_origin", "status", "start_year"):
        assert column in tv_dataset.TV_COLUMNS
        assert column in sync_tvmaze.UPDATE_SQL


def test_releases_are_found_by_their_own_prefix():
    source = inspect.getsource(tv_dataset.find_release)
    assert "RELEASE_TAG_PREFIX" in source and "MANIFEST_NAME" in source
    assert tv_dataset.RELEASE_TAG_PREFIX == "tv-"


def test_the_install_loads_the_release_then_tops_up():
    from pipeline.dataset_install import INSTALLERS

    source = inspect.getsource(INSTALLERS["tvmaze"])
    assert source.index("tv_dataset.load(") < source.index("sync_tvmaze.sync(")
    assert "done != url" in source, "a release already loaded is not loaded again"


def test_the_workflow_publishes_under_cc_by_sa_and_keeps_the_latest_marker():
    workflow = (ROOT / ".github/workflows/tv-dataset.yml").read_text()
    assert "CC BY-SA" in workflow and "LICENSE-TVmaze.txt" in workflow
    assert "--latest=false" in workflow
    assert 'startswith("tv-")' in workflow


def test_a_load_never_overwrites_tags():
    # On an install, TVmaze rows' tags are TMDB's keywords; a release's
    # empty tags must not wipe them.
    from pipeline.tv_dataset import UPDATE_COLUMNS, UPDATE_SQL

    assert "tags" not in UPDATE_COLUMNS
    assert "tags = s.tags" not in UPDATE_SQL
