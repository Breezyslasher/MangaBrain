"""A stand-in MangaBrain server for the iOS screenshot job.

It answers the endpoints the app calls with real AniList titles (fetched
once at start from AniList's public GraphQL API) and made-up scores, so
the simulator screenshots show covers and text rather than placeholders.
It is never used by the app outside CI.
"""

import json
import re
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

QUERY = """
query {
  Page(perPage: 48) {
    media(type: ANIME, sort: SCORE_DESC, isAdult: false, format_in: [TV, MOVIE]) {
      id idMal title { romaji english native } description(asHtml: false)
      genres tags { name } format startDate { year } averageScore
      coverImage { large } countryOfOrigin status episodes
    }
  }
}
"""


def fetch_titles():
    try:
        req = urllib.request.Request(
            "https://graphql.anilist.co",
            data=json.dumps({"query": QUERY}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "MangaBrain-screenshots/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            media = json.load(resp)["data"]["Page"]["media"]
    except Exception as exc:  # the job still produces shots, just without covers
        print(f"AniList unavailable ({exc}); using placeholders", file=sys.stderr)
        media = []
    items = []
    for m in media:
        text = re.sub(r"<[^>]+>", "", m.get("description") or "")
        text = re.sub(r"~!.*?!~", "", text, flags=re.S).strip()
        items.append({
            "id": m["id"],
            "medium": "anime",
            "id_mal": m.get("idMal"),
            "title": m["title"]["romaji"],
            "title_english": m["title"].get("english"),
            "title_native": m["title"].get("native"),
            "description": text,
            "genres": m.get("genres") or [],
            "tags": [t["name"] for t in (m.get("tags") or [])][:12],
            "format": m.get("format"),
            "episodes": m.get("episodes"),
            "country_of_origin": m.get("countryOfOrigin"),
            "start_year": (m.get("startDate") or {}).get("year"),
            "status": m.get("status"),
            "average_score": m.get("averageScore"),
            "cover_image_large": (m.get("coverImage") or {}).get("large"),
            "links": [{"label": "AniList", "url": f"https://anilist.co/anime/{m['id']}"}],
        })
    if not items:
        items = [{"id": i, "medium": "anime", "title": f"Title {i}", "start_year": 2020,
                  "format": "TV", "average_score": 80, "genres": ["Drama"]} for i in range(1, 49)]
    return items


ITEMS = fetch_titles()
BY_ID = {m["id"]: m for m in ITEMS}
GENRES = sorted({g for m in ITEMS for g in m.get("genres", [])})
TAGS = sorted({t for m in ITEMS for t in m.get("tags", [])})


def recommend(seed_id, limit):
    seed = BY_ID.get(seed_id, ITEMS[0])
    others = [m for m in ITEMS if m["id"] != seed["id"]]
    results = []
    for i, m in enumerate(others[:limit]):
        sim = round(96 - i * 1.3, 1)
        results.append({"media": m, "similarity": sim,
                        "components": {"semantic": sim / 100, "tags": 0.6, "genres": 0.7}})
    related = [{"media": others[-1], "relation_type": "SEQUEL"}] if others else []
    return {"seed": seed, "weights": {"semantic": 0.5, "tags": 0.3, "genres": 0.2},
            "results": results, "related": related, "seeds": [seed]}


SETTINGS = {
    "anilist_username": "demo", "mal_username": "demo", "kitsu_username": None,
    "yamtrack_url": None, "yamtrack_token_set": False, "hardcover_token_set": False,
    "tmdb_api_key_set": True, "profile": "default",
    "enabled_media": ["anime", "manga", "light_novel", "book", "movie", "tv"],
}

DATASETS = {"datasets": [
    {"key": "anilist", "label": "Anime and manga", "description": "AniList", "media":
     ["anime", "manga", "light_novel", "one_shot"], "installable": False, "installed": True,
     "items": 187856, "embedded": 187856},
    {"key": "books", "label": "Books", "description": "Open Library", "media": ["book"],
     "download_hint": "About 3 GB.", "installable": True, "installed": True, "items": 628437,
     "embedded": 437248, "install": {"state": "running", "phase": "downloading",
                                     "bytes": 1_900_000_000, "total": 3_200_000_000}},
    {"key": "tmdb", "label": "Films and TV", "description": "TMDB and TVmaze",
     "media": ["movie", "tv"], "installable": True, "installed": True, "items": 47920,
     "embedded": 47920},
]}

LISTS = {"username": "demo", "lists": [
    {"list_type": "anime", "entry_count": 412, "fetched_at": "2026-11-20T08:00:00+00:00"},
    {"list_type": "manga", "entry_count": 136, "fetched_at": "2026-11-20T08:00:00+00:00"},
]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args, file=sys.stderr)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path.rstrip("/")
        limit = int(q.get("limit", ["60"])[0])
        offset = int(q.get("offset", ["0"])[0])
        if path == "/search":
            text = q.get("q", [""])[0].lower()
            hits = [m for m in ITEMS if text in (m.get("title_english") or m["title"]).lower()]
            return self._send({"results": hits[offset:offset + limit]})
        if path.startswith("/recommend/"):
            return self._send(recommend(int(path.rsplit("/", 1)[1]), limit))
        if path in ("/recommend", "/foryou"):
            ids = [int(i) for i in q.get("ids", [])] or [ITEMS[0]["id"]]
            return self._send(recommend(ids[0], limit))
        if path == "/random":
            return self._send(ITEMS[7 % len(ITEMS)])
        if path == "/describe":
            hits = ITEMS[:limit]
            return self._send({"facets": [q.get("q", [""])[0]], "results": [
                {"media": m, "similarity": 0.5, "facets_hit": 1, "facet_count": 1} for m in hits]})
        if path == "/settings":
            return self._send(SETTINGS)
        if path == "/facets":
            return self._send({"countries": ["CN", "JP", "KR"], "formats": ["MOVIE", "ONA", "OVA", "TV"],
                               "statuses": ["FINISHED", "RELEASING", "NOT_YET_RELEASED"]})
        if path == "/genres":
            return self._send({"genres": GENRES})
        if path == "/tags":
            return self._send({"tags": TAGS})
        if path == "/dub/languages":
            return self._send({"languages": [{"code": "en", "titles": 4210}, {"code": "es", "titles": 1890},
                                             {"code": "de", "titles": 1022}]})
        if path == "/datasets":
            return self._send(DATASETS)
        if path == "/profiles":
            return self._send({"profiles": ["default", "nick"], "current": "default"})
        if path in ("/anilist/demo", "/mal/demo"):
            return self._send(LISTS)
        return self._send({"detail": "Not Found"}, 404)

    def do_PUT(self):
        return self._send(SETTINGS)

    do_POST = do_GET


if __name__ == "__main__":
    print(f"{len(ITEMS)} titles", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", 8009), Handler).serve_forever()
