"""Rate-limited HTTP client with exponential backoff and an on-disk cache.

All external API calls (AniList, Jikan) go through this wrapper. The cache is
keyed on the full request (method, url, params, body) and is meant for
development so repeated syncs do not re-hit AniList; leave cache_dir empty in
production paths that need fresh data (e.g. MAL list refresh). Cached
responses expire after cache_ttl seconds (default 24h) so a re-run long after
the original sync fetches fresh data instead of silently replaying old pages.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

MAX_RATE_SLEEP = 120.0
DEFAULT_CACHE_TTL = 86400.0


class RateLimitedClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        cache_dir: str = "",
        cache_ttl: float = DEFAULT_CACHE_TTL,
        max_retries: int = 5,
        timeout: float = 30.0,
        user_agent: str = "MangaBrain/0.1 (self-hosted recommendation engine)",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self._next_allowed = 0.0
        headers = {"User-Agent": user_agent, "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        # follow_redirects: Kitsu moved from kitsu.io to kitsu.app and
        # redirects old URLs; other APIs redirect on trailing slashes.
        self._client = httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        cache_path = self._cache_path(method, url, params, json_body)
        if cache_path is not None and cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < self.cache_ttl:
                return json.loads(cache_path.read_text())
        data = self._request_live(method, url, params, json_body)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data))
        return data

    def close(self) -> None:
        self._client.close()

    def _request_live(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> Any:
        backoff = 2.0
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self._client.request(method, url, params=params, json=json_body)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                time.sleep(min(delay, MAX_RATE_SLEEP))
                backoff *= 2
                continue
            if resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                time.sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()
            self._respect_rate_headers(resp)
            return resp.json()
        raise RuntimeError("retries exhausted")

    def _throttle(self) -> None:
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
        self._next_allowed = time.monotonic() + self.min_interval

    def _respect_rate_headers(self, resp: httpx.Response) -> None:
        """Preemptively back off when the API says the window is exhausted."""
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is None or not remaining.isdigit() or int(remaining) > 0:
            return
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            delay = max(0.0, int(reset) - time.time())
        else:
            delay = 60.0
        self._next_allowed = time.monotonic() + min(delay, MAX_RATE_SLEEP)

    def _cache_path(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(
            json.dumps([method, url, params, json_body], sort_keys=True, default=str).encode()
        ).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json"
