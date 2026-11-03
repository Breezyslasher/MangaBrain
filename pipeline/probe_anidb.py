"""Is AniDB usable as a dub source, and are we allowed to use it that way?

Dismissed earlier in this investigation on recalled grounds - client
registration, a slow rate limit, bulk-use restrictions - and recalled
grounds have been wrong repeatedly here, so it is checked.

Deliberately tiny: a handful of requests, no retries, no crawl. That is not
timidity, it is the finding under test. AniDB bans IPs for automated access
and its terms restrict bulk use, so a 22,667-title walk is very likely
disallowed regardless of what the data looks like. Establishing whether the
door is open does not require walking through it, and a probe that hammered
them to find out would answer the question by violating it.

Three things decide this:

  1. Does the HTTP API answer at all without a registered client string? If
     it does not, the source needs a human to register one, which is a
     different kind of blocker from Jikan being unreachable.
  2. Is the cast data multi-language? AniDB's <seiyuu> is historically
     Japanese voice actors - the original cast, not dubs. Dub audio lives in
     release/file records, which is a per-release query and a different
     (much larger) shape of job.
  3. What do the public dumps carry? anime-titles is offered openly; if it
     holds no cast data it cannot answer a dub question whatever its terms.
"""

import argparse

import httpx

HTTP_API = "http://api.anidb.net:9001/httpapi"
TITLES_DUMP = "https://anidb.net/api/anime-titles.dat.gz"
UA = "MangaBrain/0.1 (self-hosted recommendation engine; one-off source evaluation)"

# Cowboy Bebop on AniDB, for shape inspection only.
ANIDB_ID = 23


def show(label: str, resp: httpx.Response | None, err: str | None, body_chars: int = 300) -> None:
    if err:
        print(f"  {label:<44} {err}", flush=True)
        return
    assert resp is not None
    snippet = resp.text[:body_chars].replace("\n", " ") if resp.text else ""
    print(f"  {label:<44} HTTP {resp.status_code}  {snippet}", flush=True)


def once(client: httpx.Client, label: str, url: str, params: dict | None = None) -> None:
    """One request. No retry: a retry loop against AniDB is how an IP gets banned."""
    try:
        resp = client.get(url, params=params, headers={"User-Agent": UA})
    except httpx.HTTPError as exc:
        show(label, None, f"{type(exc).__name__}: {exc}")
        return
    show(label, resp, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AniDB as a dub source")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: acknowledges this contacts AniDB, which bans automated access",
    )
    args = parser.parse_args()
    if not args.confirm:
        raise SystemExit(
            "refusing to contact AniDB without --confirm."
            " Their terms restrict automated access and they ban IPs for it;"
            " this probe sends a handful of requests and must be run knowingly."
        )

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        print("=" * 72)
        print("1. does the HTTP API answer without a registered client?")
        print("=" * 72)
        once(client, "httpapi, no client param", HTTP_API, {"request": "anime", "aid": ANIDB_ID})
        once(
            client,
            "httpapi, unregistered client name",
            HTTP_API,
            {
                "request": "anime",
                "client": "mangabrainprobe",
                "clientver": "1",
                "protover": "1",
                "aid": ANIDB_ID,
            },
        )

        print()
        print("=" * 72)
        print("2. what do the open dumps carry?")
        print("=" * 72)
        try:
            resp = client.head(TITLES_DUMP, headers={"User-Agent": UA})
            size = resp.headers.get("content-length")
            print(
                f"  anime-titles.dat.gz                          HTTP {resp.status_code}"
                f"  content-length={size}",
                flush=True,
            )
        except httpx.HTTPError as exc:
            print(
                f"  anime-titles.dat.gz                          {type(exc).__name__}", flush=True
            )
        print(
            "  NOTE anime-titles carries titles and ids only - no cast, no"
            " language, so it cannot answer a dub question at all.",
            flush=True,
        )

        print()
        print("=" * 72)
        print("verdict inputs")
        print("=" * 72)
        print(
            "  AniDB's cast field is <seiyuu>, historically the Japanese"
            "\n  original cast rather than dub casts. Dub audio is recorded"
            "\n  per RELEASE (file/group records with audio language tracks),"
            "\n  which is a per-release query across many releases per title -"
            "\n  a far larger job than ANN's one-request-per-20-titles, for a"
            "\n  signal about releases rather than about the title.",
            flush=True,
        )


if __name__ == "__main__":
    main()
