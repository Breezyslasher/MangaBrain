"""The names the AniList dataset workflow must keep, whatever it is called.

Its display name changed when books got their own dataset. These four did
not, and each one has something depending on it that a rename would break
without any error at the point of the rename.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / ".github/workflows/dataset-publish.yml"


def spec():
    return yaml.safe_load(FLOW.read_text())


def steps():
    return {s["name"]: s for s in spec()["jobs"]["build-dataset"]["steps"]}


def test_the_display_name_says_what_the_dataset_holds():
    assert spec()["name"].startswith("AniList dataset")


def test_the_asset_every_install_seeds_from_is_still_called_this():
    """releases/latest/download/mangabrain-dataset.dump is in every
    install's compose file. A different asset name 404s them all."""
    run = steps()["Publish release"]["run"]
    assert "gh release create" in run and " mangabrain-dataset.dump " in run
    compose = (ROOT / "docker-compose.prebuilt.yml").read_text()
    assert "releases/latest/download/mangabrain-dataset.dump" in compose


def test_the_resume_dispatches_this_file_by_its_file_name():
    """The file name is how the chain finds its next job. Renaming the
    file without this would end every multi-job build after one job."""
    assert f"gh workflow run {FLOW.name}" in steps()["Dispatch resume run"]["run"]


def test_the_tag_prefix_both_prune_steps_key_on():
    assert 'TAG="dataset-' in steps()["Publish release"]["run"]
    assert 'startswith("dataset-")' in steps()["Prune old snapshots"]["run"]


def test_the_checkpoint_cache_prefix():
    """A new prefix would orphan the saved checkpoint, and the next run
    would rebuild it from a 272-minute AniList sync."""
    restore = steps()["Restore checkpoint from a previous run"]["with"]
    assert restore["restore-keys"].strip() == "dataset-checkpoint-"
    assert restore["key"].startswith("dataset-checkpoint-")
