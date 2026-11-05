"""Every workflow file has to be valid YAML.

Caught writing one: an unquoted colon inside an input description -
"rows to read: 0 reads the whole dump" - made the file unparseable.
GitHub reports that as a silently missing workflow rather than an
error, and a workflow that cannot be dispatched looks exactly like one
that was never merged. This project has already lost a round trip to
that confusion.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def test_there_are_workflows_to_check():
    assert WORKFLOWS, "expected .github/workflows to hold something"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_workflow_parses(path):
    document = yaml.safe_load(path.read_text())
    assert isinstance(document, dict), path.name
    # "on" is parsed as the boolean True by YAML 1.1, which is why the
    # other tests in this suite index it that way.
    assert True in document or "on" in document, f"{path.name} declares no trigger"
    assert document.get("jobs"), f"{path.name} declares no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_timeout(path):
    """A job with no timeout runs to GitHub's six-hour ceiling before it
    is killed, and on this repository that ceiling is already load
    bearing: dataset-publish orders its steps so the checkpoint is saved
    before anything slow runs."""
    document = yaml.safe_load(path.read_text())
    for name, job in (document.get("jobs") or {}).items():
        assert job.get("timeout-minutes"), f"{path.name}:{name} has no timeout-minutes"
