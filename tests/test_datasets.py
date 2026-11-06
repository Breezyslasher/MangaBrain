def test_counts_avoid_joining_every_media_row():
    """The old single join read every wide media row against embeddings,
    and the Media page waited seconds for it on a 1.2-million-row NAS."""
    from api import datasets

    assert "JOIN" not in datasets.ITEMS_SQL.upper()
    assert datasets.EMBEDDED_SQL.strip().upper().startswith("SELECT M.SOURCE")
    assert "FROM EMBEDDINGS E JOIN MEDIA M" in " ".join(datasets.EMBEDDED_SQL.upper().split())


def test_stale_counts_are_served_while_a_thread_refreshes(monkeypatch):
    import api.routers.datasets as router

    calls = []
    monkeypatch.setattr(
        router, "counts", lambda conn, model: calls.append(1) or {"anilist": (1, 1)}
    )
    monkeypatch.setattr(router, "_counts_cache", (0.0, {"anilist": (5, 5)}))
    started = []
    monkeypatch.setattr(
        router.threading,
        "Thread",
        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})(),
    )
    assert router.catalogue_counts(object()) == {"anilist": (5, 5)}, "stale, served at once"
    assert started == ["dataset-counts"] and calls == []
    router._refreshing.release()
