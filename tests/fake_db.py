"""A stand-in connection pool for tests that exercise router logic.

CI runs pytest with no database, so routers are tested by swapping their
get_pool for one of these: `responder(sql, params)` returns the rows a real
query would have produced, and the recorded queries can be asserted on.
"""

from collections.abc import Callable
from typing import Any


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, responder: Callable[[str, Any], list[dict]]) -> None:
        self._responder = responder
        self.queries: list[tuple[str, Any]] = []

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.queries.append((sql, params))
        return FakeCursor(self._responder(sql, params))

    def commit(self) -> None:
        pass


class FakePool:
    def __init__(self, responder: Callable[[str, Any], list[dict]]) -> None:
        self.conn = FakeConn(responder)

    def connection(self) -> FakeConn:
        return self.conn
