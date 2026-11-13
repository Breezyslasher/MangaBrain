"""For you no longer counts every embedding on every request."""


def test_for_you_counts_the_embedding_versions_once_per_ten_minutes():
    from api.routers import foryou

    calls = []

    class Conn:
        def execute(self, sql, params):
            calls.append(sql)
            return type("R", (), {"fetchone": lambda self: {"embed_model": "m#v"}})()

    foryou._dominant = None
    assert foryou.dominant_model(Conn(), now=0.0) == "m#v"
    assert foryou.dominant_model(Conn(), now=599.0) == "m#v"
    assert len(calls) == 1
    foryou.dominant_model(Conn(), now=601.0)
    assert len(calls) == 2
    foryou._dominant = None
