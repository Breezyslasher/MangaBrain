"""The English cast walk fetches a page of titles per request, not one.

This is the correction of a real and expensive mistake. The walk made one
request per title - 22,167 against a two-second floor, 12.3 hours, which
put it out of reach of CI entirely - because an early probe returned zero
English voice actors for every title it sampled and that was read as
"AniList will not populate a nested connection across a page of parents".

The probe had also omitted `node { id }`, and voiceActors resolves to null
without it, silently. The fix changed the paging and the node selection
together, so the per-title shape was credited for what the node fixed.

Measured against AniList (pipeline.probe_paged_cast): a page of fifty
returns byte-identical cast data to fifty single requests, agreeing on
8/8 titles at perPage 50, 25 and 10; a request carrying fifty real ids
came back with all fifty populated and no nulls; and omitting node zeroes
the single-title shape too. These tests pin the behaviour that follows.
"""

import httpx
import pytest

from pipeline.backfill_dub import (
    CAST_CHUNK,
    CAST_PAGE_QUERY,
    CAST_QUERY,
    NullVoiceActors,
    parse_cast,
)


def cast_edges(english: int, characters: int = 3, group: str | None = None):
    edges = []
    for i in range(characters):
        actors = [{"id": 900 + i}] if i < english else []
        roles = [{"dubGroup": group}] if group and i < english else []
        edges.append({"node": {"id": i}, "voiceActors": actors, "voiceActorRoles": roles})
    return {"characters": {"edges": edges}}


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records statements instead of running them; the suite has no database."""

    def __init__(self, candidate_ids):
        self.candidate_ids = candidate_ids
        self.statements = []
        self.updates = {}
        self.recorded = []
        self.deleted = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "pg_try_advisory_lock" in sql:
            return FakeResult([(True,)])
        if "pg_advisory_unlock" in sql:
            return FakeResult([(True,)])
        if sql.strip().startswith("SELECT m.id"):
            return FakeResult([(i,) for i in self.candidate_ids])
        if "UPDATE media" in sql:
            self.updates[params["id"]] = params
            return FakeResult([])
        if "INSERT INTO dub_backfill_state" in sql:
            self.recorded.append(params)
            return FakeResult([])
        if "DELETE FROM dub_backfill_state" in sql:
            self.deleted.append(params[0])
            return FakeResult([])
        return FakeResult([])

    def commit(self):
        self.commits += 1


class FakeClient:
    """Serves cast pages and records exactly what was asked for."""

    def __init__(self, casts, fail_chunks_containing=(), graphql_error_chunks=()):
        self.casts = casts
        self.fail_ids = set(fail_chunks_containing)
        self.graphql_error_ids = set(graphql_error_chunks)
        self.requests = []

    def request(self, _method, _url, json_body=None):
        variables = json_body["variables"]
        self.requests.append(json_body)
        if "ids" in variables:
            ids = variables["ids"]
            if self.fail_ids & set(ids):
                raise httpx.ConnectError("boom")
            if self.graphql_error_ids & set(ids):
                return {"errors": [{"message": "complexity"}]}
            media = [{"id": i, **self.casts[i]} for i in ids if i in self.casts]
            return {"data": {"Page": {"media": media}}}
        media_id = variables["id"]
        if media_id in self.fail_ids:
            raise httpx.ConnectError("boom")
        if media_id not in self.casts:
            return {"data": {"Media": None}}
        return {"data": {"Media": {"id": media_id, **self.casts[media_id]}}}


def run(ids, casts, **client_kwargs):
    from pipeline.backfill_dub import backfill

    conn = FakeConn(ids)
    client = FakeClient(casts, **client_kwargs)
    checked = backfill(client, conn)
    return conn, client, checked


def test_the_query_pages_over_media_and_keeps_node():
    """node is the field whose absence caused the original false zero."""
    assert "Page(" in CAST_PAGE_QUERY
    assert "id_in: $ids" in CAST_PAGE_QUERY
    assert "node { id }" in CAST_PAGE_QUERY
    assert "voiceActors(language: ENGLISH)" in CAST_PAGE_QUERY
    # And the single-title form kept for the fallback needs it just as much:
    # omitting it zeroes that shape too, which is what misdirected the
    # original diagnosis.
    assert "node { id }" in CAST_QUERY


def test_a_whole_chunk_costs_one_request():
    """The cost claim itself: ceil(N/CAST_CHUNK) requests, not N."""
    ids = list(range(1, 121))
    casts = {i: cast_edges(english=2) for i in ids}
    _conn, client, checked = run(ids, casts)
    assert checked == 120
    expected = -(-len(ids) // CAST_CHUNK)  # ceil
    assert len(client.requests) == expected == 3
    assert len(client.requests) < len(ids)


def test_per_page_always_matches_the_chunk_sent():
    """A perPage below the id count truncates the response without saying so.

    That would skip titles while reporting success - the same shape of
    silent wrong answer as the original null-cast bug.
    """
    ids = list(range(1, 76))
    casts = {i: cast_edges(english=1) for i in ids}
    _conn, client, _checked = run(ids, casts)
    for body in client.requests:
        variables = body["variables"]
        assert variables["perPage"] == len(variables["ids"])


def test_counts_are_written_per_title_from_one_page():
    ids = [10, 11, 12]
    casts = {
        10: cast_edges(english=2, characters=4, group="Funimation"),
        11: cast_edges(english=0, characters=5),
        12: cast_edges(english=1, characters=1),
    }
    conn, client, checked = run(ids, casts)
    assert len(client.requests) == 1
    assert checked == 3
    assert conn.updates[10]["english"] == 2
    assert conn.updates[10]["characters"] == 4
    assert conn.updates[10]["groups"] == ["Funimation"]
    # Checked and genuinely empty stays 0, distinct from never checked.
    assert conn.updates[11]["english"] == 0
    assert conn.updates[11]["characters"] == 5
    assert conn.updates[12]["english"] == 1


def test_an_id_absent_from_the_page_is_recorded_not_written():
    """AniList returns only what it found; the gap must not look checked."""
    ids = [20, 21, 22]
    casts = {20: cast_edges(english=1), 22: cast_edges(english=1)}
    conn, _client, checked = run(ids, casts)
    assert checked == 2
    assert 21 not in conn.updates
    statuses = {(r["id"], r["status"]) for r in conn.recorded}
    assert (21, "no_media") in statuses


def test_a_failed_chunk_is_retried_one_title_at_a_time():
    """One bad id must not cost its forty-nine neighbours their turn."""
    ids = [30, 31, 32, 33]
    casts = {i: cast_edges(english=1) for i in ids}
    conn, client, checked = run(ids, casts, fail_chunks_containing=[32])
    # The chunk request failed, then each title was asked for singly.
    assert any("ids" in b["variables"] for b in client.requests)
    singles = [b for b in client.requests if "id" in b["variables"]]
    assert len(singles) == 4
    # The three good ids still got written despite sharing a chunk with 32.
    assert checked == 3
    assert set(conn.updates) == {30, 31, 33}
    assert any(r["id"] == 32 for r in conn.recorded)


def test_graphql_errors_on_a_chunk_also_fall_back():
    ids = [40, 41]
    casts = {i: cast_edges(english=1) for i in ids}
    conn, client, checked = run(ids, casts, graphql_error_chunks=[40])
    singles = [b for b in client.requests if "id" in b["variables"]]
    assert len(singles) == 2
    assert checked == 2
    assert set(conn.updates) == {40, 41}


def test_null_voice_actors_still_raises():
    """The guard against the exact bug that caused the false zero."""
    media = {"characters": {"edges": [{"node": {"id": 1}, "voiceActors": None}]}}
    with pytest.raises(NullVoiceActors):
        parse_cast(media)


def test_a_second_walk_declines_to_duplicate_the_first():
    from pipeline.backfill_dub import backfill

    class Locked(FakeConn):
        def execute(self, sql, params=None):
            if "pg_try_advisory_lock" in sql:
                return FakeResult([(False,)])
            return super().execute(sql, params)

    conn = Locked([1, 2, 3])
    client = FakeClient({1: cast_edges(1)})
    assert backfill(client, conn) == 0
    assert client.requests == []
