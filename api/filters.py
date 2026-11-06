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
    min_episodes: int | None = None,
    min_chapters: int | None = None,
    dubbed: bool = False,
    dub_lang: str | None = None,
    mal_user: str | None = None,
    anilist_user: str | None = None,
    exclude_lists: list[str] | None = None,
    keep_planned: bool = False,
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
    # An unknown year passes both bounds, the same rule the length bounds
    # follow and for the same reason: missing data is not evidence against
    # a title. It mattered little while AniList supplied a year for almost
    # everything. It matters now - a work in Open Library carries no
    # reliable date, because publication belongs to its editions, so about
    # four books in five have none, and a bare >= silently excluded every
    # one of them from any year-filtered search.
    if year_min is not None:
        clauses.append("(m.start_year IS NULL OR m.start_year >= %(f_year_min)s)")
        params["f_year_min"] = year_min
    if year_max is not None:
        clauses.append("(m.start_year IS NULL OR m.start_year <= %(f_year_max)s)")
        params["f_year_max"] = year_max
    # Unscored passes, for the third time and the same reason. Every book
    # has a NULL average_score, so a bare >= emptied the Books tab the
    # moment the score slider moved.
    #
    # Books have no score because the works dump this project imports
    # carries none: a work record holds title, subjects, description and
    # a first-publish date, and nothing resembling a rating (see
    # pipeline.sync_openlibrary for every field read). A score would mean
    # a second source joined on the work key, and the reason not to reach
    # for one is coverage rather than principle - an average rating is a
    # quality signal, not a popularity signal, which is why AniList's
    # averageScore is stored and filterable here. But a score present on
    # a small, well-known slice of the catalog and absent everywhere else
    # is worse than no score at all: a floor over it would rank nothing
    # and exclude almost everything, passing for a quality filter while
    # acting as a popularity one. That is the exact failure this project
    # exists to avoid, so the bar was a measured coverage figure rather
    # than availability.
    #
    # Measured, 6 Nov 2026, against 636,000 imported books and confirmed
    # on an independent 70,121-work sample of the dump
    # (pipeline.survey_openlibrary_ratings): 18.19% carry any rating,
    # 2.39% carry three, 1.33% carry five. Of the 115,683 rated books,
    # 87% rest on one or two votes - 1,069,316 ratings spread over
    # 711,505 works is 1.5 votes each. The 1.33% with five votes are the
    # well-known ones: a book that clears the description gate is 10.5x
    # likelier to be rated than a work drawn at random, so a floor over
    # this column would select for fame, not quality. And where ratings
    # do exist they mean 3.98 out of 5, compressed at the top because
    # people rate what they finished and liked, so a floor over them
    # separates almost nothing anyway. Four reasons pointing one way:
    # there is no score column to build here.
    if min_score is not None:
        clauses.append("(m.average_score IS NULL OR m.average_score >= %(f_min_score)s)")
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
    # Floors, mirroring the caps. Only max existed, which was an omission
    # rather than a decision: "something long to sink into" is as ordinary a
    # request as "something short", and with only a cap there was no way to
    # express it.
    #
    # Unknown lengths stay included here too, for the same reason and with
    # the same cost. An absent episode count is not evidence the title is
    # short, exactly as it is not evidence it is long - the asymmetric
    # alternative would hide every still-releasing series from a floor while
    # a cap keeps them, and the project's rule throughout is that missing
    # data is not evidence against a title (the dub filter is positive-only
    # for this reason, and english_va_count = 0 is never served as sub-only).
    if min_episodes is not None:
        clauses.append("(m.episodes IS NULL OR m.episodes >= %(f_min_episodes)s)")
        params["f_min_episodes"] = min_episodes
    if min_chapters is not None:
        clauses.append("(m.chapters IS NULL OR m.chapters >= %(f_min_chapters)s)")
        params["f_min_chapters"] = min_chapters
    # dubbed=true is English, kept as the shorthand it has always been.
    if dubbed and not dub_lang:
        dub_lang = "en"
    if dub_lang:
        # Positive only, and deliberately not invertible. english_va_count > 0
        # means AniList credits an English voice actor, which is a reliable
        # "has an English dub". A zero is NOT a reliable "sub only": midlist
        # titles with real dubs come back empty (Berserk 2 (2017): 21
        # characters, 0 English voice actors), and NULL just means not yet
        # backfilled. There is no dubbed=False branch for that reason, and
        # adding one would quietly hide dubbed titles from the obscure end of
        # the catalog this project exists to surface.
        # Measured or inferred. The inferred flag comes from a franchise
        # relative with a measured English cast (pipeline.propagate_dub),
        # which is what recovers season and OVA entries whose own AniList
        # page never had the English cast entered.
        code = dub_lang.strip().lower()
        if code == "en":
            # English has two sources and they arrive at different times.
            # AniList measures it per entry over ~22k requests and hours;
            # ANN supplies every language at once but only for the titles it
            # can be matched to. Either answers for English, so the filter
            # does not go empty while one of them is still filling.
            clauses.append(
                "(m.dub_languages @> ARRAY['en']::text[]"
                " OR m.measured_dub_languages @> ARRAY['en']::text[]"
                " OR m.mal_dub_languages @> ARRAY['en']::text[]"
                " OR m.english_va_count > 0 OR m.english_dub_inferred)"
            )
        else:
            # Two sources, and they differ in kind rather than only in
            # timing. measured_dub_languages is AniList answering for the id
            # it was given, so it reaches the whole catalog;
            # dub_languages is ANN matched on title, which resolves 31.8% and
            # can in principle match the wrong show. Either satisfies the
            # filter - withholding a title because only the weaker source
            # knows about it would hide exactly the obscure entries this
            # project exists to surface - and the API reports which answered.
            clauses.append(
                "(m.dub_languages @> ARRAY[%(f_dub_lang)s]::text[]"
                " OR m.measured_dub_languages @> ARRAY[%(f_dub_lang)s]::text[]"
                " OR m.mal_dub_languages @> ARRAY[%(f_dub_lang)s]::text[])"
            )
            params["f_dub_lang"] = code
    # keep_planned: plan-to-watch/plan-to-read entries stop excluding, so
    # planned titles can still be recommended; started and finished entries
    # (watching/reading, completed, on hold, dropped) always exclude.
    if mal_user:
        # MAL anime and manga ids are separate id spaces (anime #1 and manga #1
        # are different titles), so a list entry only excludes rows of the
        # matching media type: list_type anime <-> media_type ANIME, manga <->
        # MANGA (which covers manhwa, manhua, light novels, and one-shots).
        # Two status vocabularies, because the source changed. The official
        # MAL API sends strings ("plan_to_watch", "plan_to_read"); Jikan,
        # which this used to read, sent numeric codes where 6 was plan to
        # watch/read. Rows cached under either shape must honour the toggle,
        # and a list cached before the switch is not re-fetched just because
        # the integration changed underneath it.
        #
        # The official vocabulary in full, so the next reader does not have
        # to guess at the spelling of a value this clause matches exactly:
        # completed, watching, reading, plan_to_watch, plan_to_read,
        # on_hold, dropped. Only the two plan_ values mean not-started.
        # Confirmed against a working client (Yamtrack's importer) rather
        # than recalled - a wrong spelling here fails silently, since MAL
        # answers 200 to anything it does not recognise.
        #
        # Unknown status stays excluded: both syncs coerce a missing status
        # to "", which matches neither planned value.
        planned_guard = (
            " AND l.status NOT IN ('6', 'plan_to_watch', 'plan_to_read')" if keep_planned else ""
        )
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM mal_list_entries l"
            " WHERE l.username = %(f_mal_user)s AND l.id_mal = m.id_mal"
            f" AND l.list_type = lower(m.media_type){planned_guard})"
        )
        params["f_mal_user"] = mal_user.strip().lower()
    if anilist_user:
        # AniList exclusion matches the AniList media id directly. Status is
        # the MediaListStatus enum; PLANNING is its only not-started value.
        planned_guard = " AND al.status <> 'PLANNING'" if keep_planned else ""
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM anilist_list_entries al"
            f" WHERE al.username = %(f_anilist_user)s AND al.media_id = m.id{planned_guard})"
        )
        params["f_anilist_user"] = anilist_user.strip().lower()
    names = [n.strip().lower() for n in (exclude_lists or []) if n.strip()]
    if names:
        # Generic named exclusion lists (see /exclusions endpoints), combined:
        # a row on ANY of the named lists is excluded. AniList ids match the
        # row id directly, MAL ids by kind-typed id_mal join.
        planned_guard = " AND NOT ce.planned" if keep_planned else ""
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM custom_exclusion_entries ce"
            " WHERE ce.list_name = ANY(%(f_excl_lists)s)"
            " AND ((ce.kind = 'anilist' AND ce.ext_id = m.id)"
            " OR (ce.kind = 'mal_anime' AND m.media_type = 'ANIME' AND ce.ext_id = m.id_mal)"
            " OR (ce.kind = 'mal_manga' AND m.media_type = 'MANGA' AND ce.ext_id = m.id_mal))"
            f"{planned_guard})"
        )
        params["f_excl_lists"] = names

    sql = "".join(f" AND {clause}" for clause in clauses)
    return sql, params
