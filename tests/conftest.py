"""The API's start-up warm-ups run on background threads: the description
model would be downloaded and loaded in CI, and the catalogue counts would
fill a cache other tests read. Tests that want them use `real_warmups`."""

import pytest

from api import embedding
from api.routers import datasets

REAL = {"embedding": embedding.warm, "datasets": datasets.warm}


@pytest.fixture(autouse=True)
def _no_background_warmups(monkeypatch):
    monkeypatch.setattr(embedding, "warm", lambda: None)
    monkeypatch.setattr(datasets, "warm", lambda: None)


@pytest.fixture
def real_warmups():
    return REAL
