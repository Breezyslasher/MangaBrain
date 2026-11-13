"""What the API does in the background when it starts, so the first
sentence search and the first Media page do not wait."""

import time


def test_the_describe_model_loads_in_the_background(monkeypatch, real_warmups):
    from api import embedding
    from api.config import settings

    loaded = []
    monkeypatch.setattr(embedding, "_get_model", lambda: loaded.append(1))
    monkeypatch.setattr(settings, "preload_describe_model", True)
    real_warmups["embedding"]()
    for _ in range(100):
        if loaded:
            break
        time.sleep(0.01)
    assert loaded == [1]

    loaded.clear()
    monkeypatch.setattr(settings, "preload_describe_model", False)
    real_warmups["embedding"]()
    time.sleep(0.05)
    assert loaded == [], "PRELOAD_DESCRIBE_MODEL=false leaves it to the first search"


def test_the_release_check_never_holds_up_the_media_page(monkeypatch):
    import threading

    from api.datasets import BY_KEY
    from api.routers import datasets

    gate = threading.Event()
    calls = []

    def slow_check(key):
        calls.append(key)
        gate.wait(2)
        datasets._release_cache[key] = (time.monotonic(), True)

    monkeypatch.setattr(datasets, "_check_release", slow_check)
    monkeypatch.setattr(datasets, "_release_cache", {})
    started = time.monotonic()
    assert datasets.release_available(BY_KEY["books"]) is None, "unknown until it returns"
    assert time.monotonic() - started < 0.5
    gate.set()
    for _ in range(100):
        if datasets._release_cache:
            break
        time.sleep(0.01)
    assert datasets.release_available(BY_KEY["books"]) is True
    assert calls == ["books"]
