"""Can this engine cover books, games, screen and comics as it does anime?

The recommender needs three things per entry and nothing else: a
synopsis worth embedding, tags or genres worth comparing, and a stable
id to join on. Catalogue size, polish and brand count for nothing if the
synopsis field is usually empty - a title with no description is
embedded from its own name, which is how "Liar" and "Ghost Writer"
surfaced for "unreliable narrator".

So this measures coverage, not features. For a sample of real entries
from each source: what share carry a synopsis of at least MIN_SYNOPSIS
characters, the median length, and the median number of tags. A source
answering 40% is not a medium this engine can rank.

    python -m pipeline.probe_media_sources books
    python -m pipeline.probe_media_sources boardgames
    python -m pipeline.probe_media_sources videogames
    python -m pipeline.probe_media_sources screen
    python -m pipeline.probe_media_sources comics
    python -m pipeline.probe_media_sources all

Sources needing a key are still called, without one, so "needs a key" is
a measured refusal rather than a claim from documentation. Nothing here
writes to the database.

Runs in CI because the dev container's egress proxy refuses most of
these hosts.
"""

import argparse
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from pipeline.client import RateLimitedClient

# Below this a description is a tagline, not something to embed. The
# anime catalogue's own synopses run to several hundred characters.
MIN_SYNOPSIS = 200

# Polite, and small: this is a reconnaissance, not a harvest.
SAMPLE = 25


@dataclass
class Finding:
    source: str
    reachable: bool = False
    needs_key: bool = False
    note: str = ""
    synopsis_lengths: list[int] = field(default_factory=list)
    tag_counts: list[int] = field(default_factory=list)
    sampled: int = 0

    def report(self) -> str:
        if not self.reachable:
            return f"  {self.source:<22} unreachable        {self.note}"
        if self.needs_key:
            return f"  {self.source:<22} needs a key        {self.note}"
        if not self.sampled:
            return f"  {self.source:<22} no sample          {self.note}"
        usable = sum(1 for n in self.synopsis_lengths if n >= MIN_SYNOPSIS)
        share = usable / self.sampled
        median_len = statistics.median(self.synopsis_lengths) if self.synopsis_lengths else 0
        median_tags = statistics.median(self.tag_counts) if self.tag_counts else 0
        verdict = "usable" if share >= 0.7 else ("thin" if share >= 0.4 else "NOT usable")
        return (
            f"  {self.source:<22} {share:5.0%} with a synopsis  "
            f"median {median_len:>5.0f} chars, {median_tags:>4.0f} tags"
            f"   {verdict}   {self.note}"
        )


def text_of(value) -> str:
    """Open Library stores a description as a string or as {value: ...}."""
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def probe_open_library(client: RateLimitedClient) -> Finding:
    found = Finding("Open Library", note="free, no key, bulk dumps published")
    try:
        search = client.request(
            "GET",
            "https://openlibrary.org/search.json",
            params={"q": "subject:fiction", "limit": SAMPLE, "fields": "key"},
        )
    except (httpx.HTTPError, ValueError) as exc:
        found.note = type(exc).__name__
        return found
    found.reachable = True
    for doc in (search.get("docs") or [])[:SAMPLE]:
        key = doc.get("key")
        if not key:
            continue
        try:
            work = client.request("GET", f"https://openlibrary.org{key}.json")
        except (httpx.HTTPError, ValueError):
            continue
        found.sampled += 1
        found.synopsis_lengths.append(len(text_of(work.get("description"))))
        found.tag_counts.append(len(work.get("subjects") or []))
    return found


def probe_google_books(client: RateLimitedClient) -> Finding:
    found = Finding("Google Books", note="keyless quota is small and per-IP")
    try:
        data = client.request(
            "GET",
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": "subject:fiction", "maxResults": 40},
        )
    except (httpx.HTTPError, ValueError) as exc:
        found.note = type(exc).__name__
        return found
    found.reachable = True
    for item in (data.get("items") or [])[:SAMPLE]:
        info = item.get("volumeInfo") or {}
        found.sampled += 1
        found.synopsis_lengths.append(len(str(info.get("description") or "")))
        found.tag_counts.append(len(info.get("categories") or []))
    return found


def probe_bgg(raw: httpx.Client) -> Finding:
    """BoardGameGeek's XMLAPI2. XML, so it bypasses the JSON client."""
    found = Finding("BoardGameGeek", note="free, no key, XML, ids are stable")
    ids = ",".join(str(i) for i in range(1, SAMPLE + 1))
    try:
        resp = raw.get("https://boardgamegeek.com/xmlapi2/thing", params={"id": ids, "stats": 1})
    except httpx.HTTPError as exc:
        found.note = type(exc).__name__
        return found
    if resp.status_code == 202:
        found.note = "queued (HTTP 202); retry in a few seconds"
        return found
    if resp.status_code != 200:
        found.note = f"HTTP {resp.status_code}"
        return found
    found.reachable = True
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        found.note = f"unparseable XML: {exc}"
        return found
    for item in root.findall("item"):
        found.sampled += 1
        description = item.findtext("description") or ""
        found.synopsis_lengths.append(len(description))
        tags = [
            link
            for link in item.findall("link")
            if link.get("type") in {"boardgamecategory", "boardgamemechanic"}
        ]
        found.tag_counts.append(len(tags))
    return found


def probe_steam(client: RateLimitedClient) -> Finding:
    found = Finding("Steam store", note="undocumented, ~200 requests per 5 min")
    try:
        listing = client.request("GET", "https://api.steampowered.com/ISteamApps/GetAppList/v2/")
    except (httpx.HTTPError, ValueError) as exc:
        found.note = type(exc).__name__
        return found
    found.reachable = True
    apps = (listing.get("applist") or {}).get("apps") or []
    found.note = f"{len(apps)} app ids listed; {found.note}"
    # Evenly spaced rather than the first N: the head of the list is old
    # test entries with no store page at all.
    step = max(len(apps) // (SAMPLE * 4), 1)
    checked = 0
    for app in apps[::step]:
        if checked >= SAMPLE:
            break
        try:
            detail = client.request(
                "GET",
                "https://store.steampowered.com/api/appdetails",
                params={"appids": app["appid"], "l": "english"},
            )
        except (httpx.HTTPError, ValueError):
            continue
        entry = (detail or {}).get(str(app["appid"])) or {}
        if not entry.get("success"):
            continue  # delisted or not a store item
        data = entry.get("data") or {}
        checked += 1
        found.sampled += 1
        found.synopsis_lengths.append(len(str(data.get("short_description") or "")))
        found.tag_counts.append(len(data.get("genres") or []) + len(data.get("categories") or []))
    return found


def probe_keyless_refusal(raw: httpx.Client, source: str, url: str, note: str) -> Finding:
    """Call a key-gated API without one, so the gate is measured."""
    found = Finding(source, note=note)
    try:
        resp = raw.get(url)
    except httpx.HTTPError as exc:
        found.note = f"{note}; {type(exc).__name__}"
        return found
    found.reachable = True
    if resp.status_code in (401, 403):
        found.needs_key = True
        found.note = f"HTTP {resp.status_code}; {note}"
        return found
    found.note = f"HTTP {resp.status_code}; {note}"
    return found


def probe_wikidata(client: RateLimitedClient) -> Finding:
    """Films with a plot would be ideal; measure what is actually there."""
    found = Finding("Wikidata (film)", note="free, joins by IMDb/TMDB id")
    query = """
        SELECT ?film ?filmDescription (COUNT(?genre) AS ?genres) WHERE {
          ?film wdt:P31 wd:Q11424 ; wdt:P136 ?genre .
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
        } GROUP BY ?film ?filmDescription LIMIT 25
    """
    try:
        data = client.request(
            "GET",
            "https://query.wikidata.org/sparql",
            params={"query": query, "format": "json"},
        )
    except (httpx.HTTPError, ValueError) as exc:
        found.note = f"{found.note}; {type(exc).__name__}"
        return found
    found.reachable = True
    for row in (data.get("results") or {}).get("bindings") or []:
        found.sampled += 1
        found.synopsis_lengths.append(len(row.get("filmDescription", {}).get("value", "")))
        found.tag_counts.append(int(row.get("genres", {}).get("value", 0)))
    found.note = "one-line descriptions, not plots; " + found.note
    return found


def run(domains: list[str]) -> None:
    # One retry, not five. This is reconnaissance: a source needing
    # several attempts has already told us what we came to find out, and
    # the default backoff spent two and a half minutes on a rate-limited
    # Google Books before raising.
    client = RateLimitedClient(min_interval=1.0, timeout=30.0, max_retries=1)
    raw = httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine)"},
    )
    try:
        for domain in domains:
            print(f"\n{domain}", flush=True)
            for name, probe in PROBES[domain]:
                # Printed as each source finishes, and every failure
                # contained. Measured the hard way: a domain's probes were
                # built as one list, so when Google Books raised
                # RuntimeError("retries exhausted") - which no probe was
                # catching, because the client raises it rather than an
                # httpx error - it took two and a half minutes of
                # completed Open Library sampling down with it, unprinted.
                try:
                    found = probe(client, raw)
                except Exception as exc:  # noqa: BLE001 - one source must not stop the run
                    found = Finding(name, note=f"{type(exc).__name__}: {exc}")
                print(found.report(), flush=True)
    finally:
        client.close()
        raw.close()
    print(
        f"\nUsable means {MIN_SYNOPSIS}+ characters of synopsis on at least 70% of a"
        f" {SAMPLE}-entry sample. Tags are whatever the source calls genres,"
        " categories or mechanics."
    )


def gated(source: str, url: str, note: str):
    """A key-gated source, as a (name, probe) pair like the others."""
    return source, lambda c, r: probe_keyless_refusal(r, source, url, note)


# (display name, probe) per source, so the runner can name a source that
# fails before its Finding exists, and print each result as it arrives.
PROBES: dict[str, list[tuple]] = {
    "books": [
        ("Open Library", lambda c, r: probe_open_library(c)),
        ("Google Books", lambda c, r: probe_google_books(c)),
    ],
    "boardgames": [("BoardGameGeek", lambda c, r: probe_bgg(r))],
    "videogames": [
        ("Steam store", lambda c, r: probe_steam(c)),
        gated("IGDB", "https://api.igdb.com/v4/games", "Twitch client id + secret, free"),
        gated("RAWG", "https://api.rawg.io/api/games", "free key, 20k/month"),
    ],
    "screen": [
        gated("TMDB", "https://api.themoviedb.org/3/movie/550", "free key, no review"),
        ("Wikidata (film)", lambda c, r: probe_wikidata(c)),
    ],
    "comics": [
        gated(
            "Comic Vine",
            "https://comicvine.gamespot.com/api/issues/?format=json",
            "free key, 200 requests/hour",
        ),
        gated("Metron", "https://metron.cloud/api/issue/", "free account, basic auth"),
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure other media sources")
    parser.add_argument("domain", choices=[*PROBES, "all"])
    args = parser.parse_args()
    run(list(PROBES) if args.domain == "all" else [args.domain])


if __name__ == "__main__":
    main()
