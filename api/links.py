"""Links to each title's page on the site its data came from.

Every result used to link to anilist.co/<kind>/<catalogue id>. That is
right for AniList rows, whose catalogue id is AniList's, and wrong for
everything else: films, shows and books take ids from MangaBrain's own
sequence, so the link opened an unrelated AniList entry or a missing page.
"""

from typing import Any

from api.models import Link


def links_for(row: dict[str, Any]) -> list[Link]:
    source = row.get("source") or "anilist"
    source_id = str(row.get("source_id") or row.get("id") or "")
    links: list[Link] = []
    if source == "anilist":
        kind = "anime" if row.get("medium") == "anime" else "manga"
        links.append(Link(label="AniList", url=f"https://anilist.co/{kind}/{source_id}"))
        if row.get("id_mal"):
            links.append(
                Link(label="MyAnimeList", url=f"https://myanimelist.net/{kind}/{row['id_mal']}")
            )
    elif source == "tmdb":
        # "movie/603" or "tv/1399", which is also TMDB's own path.
        kind, _, number = source_id.partition("/")
        if kind in ("movie", "tv") and number:
            links.append(Link(label="TMDB", url=f"https://www.themoviedb.org/{kind}/{number}"))
    elif source == "tvmaze":
        links.append(Link(label="TVmaze", url=f"https://www.tvmaze.com/shows/{source_id}"))
    elif source == "openlibrary":
        # The work key, "/works/OL45804W".
        key = source_id if source_id.startswith("/") else f"/works/{source_id}"
        links.append(Link(label="Open Library", url=f"https://openlibrary.org{key}"))
    return links
