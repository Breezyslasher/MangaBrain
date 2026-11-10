"""Making room for a medium AniList does not supply.

media.id has always been the AniList id, and it is in URLs, in
embeddings.media_id and in media_relations. A second source cannot
renumber it without breaking links people already hold, so the id stays
what it is and every row records where it came from instead.
"""

from pathlib import Path

SCHEMA = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


def test_existing_ids_are_not_renumbered():
    """Provenance is added beside the id, not in place of it."""
    assert "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anilist'" in SCHEMA
    assert "ADD COLUMN IF NOT EXISTS source_id TEXT" in SCHEMA
    assert "UPDATE media SET source_id = id::text WHERE source_id IS NULL" in SCHEMA


def test_one_row_per_source_and_source_id():
    """Two sources can both call something 42; the pair is the key."""
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_media_source ON media (source, source_id)" in SCHEMA
    )


def test_other_sources_get_ids_above_anilists_range():
    assert "CREATE SEQUENCE IF NOT EXISTS media_external_id_seq START WITH 1000000000" in SCHEMA


def test_the_id_column_is_not_widened():
    """INTEGER tops out at 2,147,483,647, so a sequence from 1e9 leaves
    1.1 billion ids against Open Library's ~40 million works - and
    retyping the column would rewrite the table and its TOAST under an
    exclusive lock inside ensure_schema(), at API startup. That mistake
    was made here once with a generated column and reverted."""
    assert "ALTER COLUMN id TYPE BIGINT" not in SCHEMA
    assert "id                  INTEGER PRIMARY KEY" in SCHEMA


def test_the_vocabularies_admit_books_for_new_and_existing_databases():
    """The inline CHECK only reaches a fresh database; an existing one
    keeps the constraint it was built with until it is re-stated."""
    assert "CHECK (media_type IN ('ANIME', 'MANGA', 'BOOK', 'MOVIE', 'TV'))" in SCHEMA
    assert SCHEMA.count("'BOOK'") >= 2, "inline for new databases, re-stated for existing ones"
    assert SCHEMA.count("'book'") >= 2
    assert "DROP CONSTRAINT IF EXISTS media_medium_check" in SCHEMA


def test_books_are_their_own_group_not_a_light_novel_variant():
    from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS, group_for_medium

    assert MEDIUM_GROUPS["book"] == ["book"]
    assert "book" in ALL_MEDIUMS
    assert group_for_medium("book") == ["book"]
    assert "book" not in MEDIUM_GROUPS["light_novel"]


def test_books_share_the_embedding_space():
    """One table with a discriminator, not one table per medium: that is
    what makes cross-media similarity free, and it is the feature a
    separate books database would cost."""
    assert "CREATE TABLE IF NOT EXISTS media" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS book_media" not in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS book_embeddings" not in SCHEMA


def test_both_clients_offer_books():
    """The API has served medium=book since the schema change; until the
    clients offer it, nothing a user can press reaches those rows."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "web" / "index.html").read_text()
    assert 'data-medium="book"' in html

    app = (root / "web" / "app.js").read_text()
    assert '"/books": "book"' in app, "deep link, like the other media"
    assert "one_shot|book" in app, "and the recommendation hash route"

    # The tab labels live in FilterState, shared by the Discover tabs and
    # the settings screen.
    state = (root / "android/app/src/main/java/com/mangabrain/app/data/FilterState.kt").read_text()
    assert '"book" to "Books"' in state


def test_books_offer_no_format_filter():
    """Open Library has no format vocabulary - a work is a work. The format
    list comes from what a medium's titles carry, and a list of one or none
    leaves the control out on both clients."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app = (root / "web" / "app.js").read_text()
    assert "const FORMATS = {" not in app, "formats come from /facets"
    assert 'facets.formats.length > 1 ? "" : "none"' in app
    fragment = (
        root / "android/app/src/main/java/com/mangabrain/app/ui/filters/FiltersFragment.kt"
    ).read_text()
    assert "val hasFormats = known.formats.size > 1" in fragment


def test_every_api_medium_group_has_a_tab():
    """So a medium cannot be added to the API and stay unreachable."""
    from pathlib import Path

    from api.media_groups import MEDIUM_GROUPS

    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
    for group in MEDIUM_GROUPS:
        assert f'data-medium="{group}"' in html, group


# Which query parameters the Books tab must not send, and how each client
# spells the control behind it. The spellings differ - the web hides DOM
# ids, the Android client suppresses parameter keys - so the mapping is
# written out rather than inferred, because the mapping is what drifts.
BOOK_SUPPRESSED = {
    "min_score": "minScore",
    "country": "country",
    "status": "status",
    "max_popularity": "maxPop",
}
BOOK_SUPPRESSED_LENGTH = ("minLen", "maxLen")


def _unsupported_block(text: str) -> str:
    start = text.index("UNSUPPORTED")
    return text[start : text.index("}", start)]


def test_both_clients_suppress_the_same_filters_for_books():
    """Origin, Status and the length bounds do not merely get ignored for a
    book - they match no book at all, because a NULL column never equals
    'JP'. A client that still sends one empties its own grid with nothing
    on screen to explain why, so neither client may drift from the other.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    web = _unsupported_block((root / "web" / "app.js").read_text())
    android = _unsupported_block(
        (root / "android/app/src/main/java/com/mangabrain/app/data/FilterState.kt").read_text()
    )

    for param, element_id in BOOK_SUPPRESSED.items():
        assert f'"{element_id}"' in web, f"web still sends {param} for books"
        assert f'"{param}"' in android, f"android still sends {param} for books"
    for element_id in BOOK_SUPPRESSED_LENGTH:
        assert f'"{element_id}"' in web, f"web still sends {element_id} for books"
    assert '"length"' in android, "android still sends a length bound for books"


def test_a_suppressed_control_is_also_hidden():
    """A control that cannot change the result is not left on screen. The
    web hides each row through the same table that suppresses the value,
    and the Android fragment asks FilterState the same question, so the two
    cannot disagree about whether a control is live."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app = (root / "web" / "app.js").read_text()
    assert 'supports(id) && !empty ? "" : "none"' in app, "web rows hidden from the support table"
    assert "function controlValue(" in app, "and the same table gates the value"

    fragment = (
        root / "android/app/src/main/java/com/mangabrain/app/ui/filters/FiltersFragment.kt"
    ).read_text()
    assert "FilterState.supports(control)" in fragment
    for control in ("min_score", "country", "status", "max_popularity", "length"):
        assert f'rows("{control}"' in fragment, control


def test_the_filter_badge_counts_only_what_is_sent():
    """The badge reads the same singleton the query does, and a value typed
    on the Anime tab survives the switch to Books. Counting it there made
    the badge claim filters the request never carried."""
    from pathlib import Path

    state = (
        Path(__file__).resolve().parent.parent
        / "android/app/src/main/java/com/mangabrain/app/data/FilterState.kt"
    ).read_text()
    badge = state[state.index("val activeCount") : state.index("fun filterParams")]
    assert "supports(control)" in badge


def test_the_media_check_constraints_are_not_rebuilt_on_every_start():
    """The schema runs on every API start as one transaction. Dropping and
    re-adding these constraints each time held an exclusive lock and
    scanned every row; behind a busy worker that stalled startup into a
    502. They are re-stated only when the stored definition is out of
    date, and NOT VALID, so even then no existing row is scanned."""
    block = SCHEMA[SCHEMA.index("DO $$", SCHEMA.index("is missing a value, and NOT VALID")) :]
    block = block[: block.index("END $$;")]
    assert block.count("pg_get_constraintdef(oid) LIKE") == 2
    assert block.count(") NOT VALID;") == 2
    assert block.index("IF NOT EXISTS") < block.index(
        "DROP CONSTRAINT IF EXISTS media_media_type_check"
    )
