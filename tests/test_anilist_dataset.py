"""The anime catalogue's merge-format release, and its reload button."""

import inspect
from pathlib import Path

from pipeline import anilist_dataset

ROOT = Path(__file__).resolve().parent.parent


def test_only_anilist_rows_are_applied_and_only_when_newer():
    applied, update, insert = anilist_dataset.merge_sql(["source", "source_id", "title_romaji"])
    assert "s.source = 'anilist'" in applied
    assert "m.source = 'anilist'" in applied, "a non-AniList row is never overwritten"
    assert "coalesce(m.updated_at, 0) < coalesce(s.updated_at, 0)" in applied
    assert "anilist_applied" in update and "anilist_applied" in insert
    assert "source =" not in update.split("SET", 1)[1].split("FROM")[0], "identity is not rewritten"


def test_relations_are_replaced_only_for_applied_titles():
    assert "USING anilist_applied" in anilist_dataset.RELATIONS_DELETE_SQL
    assert "JOIN anilist_applied" in anilist_dataset.RELATIONS_INSERT_SQL


def test_local_embeddings_are_kept():
    assert "ON CONFLICT (media_id, embed_model) DO NOTHING" in anilist_dataset.EMBEDDINGS_SQL


def test_an_older_database_refuses_a_newer_release_by_name():
    source = inspect.getsource(anilist_dataset.load)
    assert "missing = [c for c in columns if c not in here]" in source


def test_the_reload_button_merges_and_never_restores_the_snapshot():
    from api.datasets import BY_KEY
    from pipeline.dataset_install import INSTALLERS

    assert BY_KEY["anilist"].installable
    assert BY_KEY["anilist"].update_label == "Reload from the newest release"
    install = inspect.getsource(INSTALLERS["anilist"])
    assert "anilist_dataset.load(" in install
    assert (
        "pg_restore" not in install.replace("pg_dump", "")
        and "restore" not in install.split('"""')[2]
    )


def test_the_weekly_release_carries_the_merge_copy_beside_the_snapshot():
    workflow = (ROOT / ".github/workflows/dataset-publish.yml").read_text()
    assert "python -m pipeline.anilist_dataset export --out anilist-dataset" in workflow
    assert "mangabrain-dataset.dump anilist-dataset/* --latest" in workflow
    assert anilist_dataset.RELEASE_TAG_PREFIX == "dataset-"
