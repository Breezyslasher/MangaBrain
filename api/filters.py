"""Shared SQL filter builder for recommend, random, and search."""

from typing import Any


def build_filters(
    *,
    adult: bool = False,
    formats: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    min_score: int | None = None,
    countries: list[str] | None = None,
    statuses: list[str] | None = None,
    genres_include: list[str] | None = None,
    genres_exclude: list[str] | None = None,
    tags_include: list[str] | None = None,
    tags_exclude: list[str] | None = None,
    max_popularity: int | None = None,
    max_episodes: int | None = None,
    max_chapters: int | None = None,
    mal_user: str | None = None,
    anilist_user: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (sql_fragment, params). The fragment is a chain of ' AND ...'
    clauses safe to append to a WHERE clause that already has a condition.
    Adult content is excluded at the SQL level unless the toggle is on."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if not adult:
        clauses.append("m.is_adult = FALSE")
    if formats:
        clauses.append("m.format = ANY(%(f_formats)s)")
        params["f_formats"] = [f.upper() for f in formats]
    if year_min is not None:
        clauses.append("m.start_year >= %(f_year_min)s")
        params["f_year_min"] = year_min
    if year_max is not None:
        clauses.append("m.start_year <= %(f_year_max)s")
        params["f_year_max"] = year_max
    if min_score is not None:
        clauses.append("m.average_score >= %(f_min_score)s")
        params["f_min_score"] = min_score
    if countries:
        clauses.append("m.country_of_origin = ANY(%(f_countries)s)")
        params["f_countries"] = [c.upper() for c in countries]
    if statuses:
        clauses.append("m.status = ANY(%(f_statuses)s)")
        params["f_statuses"] = [s.upper() for s in statuses]
    if genres_include:
        # Every listed genre must be present.
        clauses.append("m.genres @> %(f_genres_inc)s::text[]")
        params["f_genres_inc"] = genres_include
    if genres_exclude:
        clauses.append("NOT (m.genres && %(f_genres_exc)s::text[])")
        params["f_genres_exc"] = genres_exclude
    if tags_include:
        clauses.append(
            "COALESCE((SELECT array_agg(t->>'name') FROM jsonb_array_elements(m.tags) t), '{}')"
            " @> %(f_tags_inc)s::text[]"
        )
        params["f_tags_inc"] = tags_include
    if tags_exclude:
        clauses.append(
            "NOT (COALESCE((SELECT array_agg(t->>'name')"
            " FROM jsonb_array_elements(m.tags) t), '{}') && %(f_tags_exc)s::text[])"
        )
        params["f_tags_exc"] = tags_exclude
    if max_popularity is not None:
        # Obscurity cap: popularity is a FILTER the user opts into, never a
        # ranking input (the spec forbids it in scoring only). Entries with
        # unknown popularity count as obscure.
        clauses.append("(m.popularity IS NULL OR m.popularity <= %(f_max_pop)s)")
        params["f_max_pop"] = max_popularity
    if max_episodes is not None:
        # Unknown lengths (often still-releasing titles) stay included.
        clauses.append("(m.episodes IS NULL OR m.episodes <= %(f_max_episodes)s)")
        params["f_max_episodes"] = max_episodes
    if max_chapters is not None:
        clauses.append("(m.chapters IS NULL OR m.chapters <= %(f_max_chapters)s)")
        params["f_max_chapters"] = max_chapters
    if mal_user:
        # MAL anime and manga ids are separate id spaces (anime #1 and manga #1
        # are different titles), so a list entry only excludes rows of the
        # matching media type: list_type anime <-> media_type ANIME, manga <->
        # MANGA (which covers manhwa, manhua, light novels, and one-shots).
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM mal_list_entries l"
            " WHERE l.username = %(f_mal_user)s AND l.id_mal = m.id_mal"
            " AND l.list_type = lower(m.media_type))"
        )
        params["f_mal_user"] = mal_user.strip().lower()
    if anilist_user:
        # AniList exclusion matches the AniList media id directly.
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM anilist_list_entries al"
            " WHERE al.username = %(f_anilist_user)s AND al.media_id = m.id)"
        )
        params["f_anilist_user"] = anilist_user.strip().lower()

    sql = "".join(f" AND {clause}" for clause in clauses)
    return sql, params
