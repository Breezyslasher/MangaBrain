import os
import time

from pipeline.client import RateLimitedClient


def test_cache_hits_within_ttl_and_expires_after(tmp_path, monkeypatch):
    client = RateLimitedClient(min_interval=0, cache_dir=str(tmp_path), cache_ttl=100)
    calls = []

    def fake_live(*_args):
        calls.append(1)
        return {"call": len(calls)}

    monkeypatch.setattr(client, "_request_live", fake_live)

    first = client.request("GET", "http://example.test/api")
    second = client.request("GET", "http://example.test/api")
    assert first == second == {"call": 1}
    assert len(calls) == 1

    # Age the cache file past the TTL; the next request must go live again.
    path = client._cache_path("GET", "http://example.test/api", None, None)
    old = time.time() - 200
    os.utime(path, (old, old))
    third = client.request("GET", "http://example.test/api")
    assert third == {"call": 2}
    assert len(calls) == 2


def test_no_cache_dir_always_goes_live(monkeypatch):
    client = RateLimitedClient(min_interval=0)
    calls = []
    monkeypatch.setattr(client, "_request_live", lambda *_a: calls.append(1) or {"n": len(calls)})
    client.request("GET", "http://example.test/api")
    client.request("GET", "http://example.test/api")
    assert len(calls) == 2
