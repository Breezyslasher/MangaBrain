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
    assert "CHECK (media_type IN ('ANIME', 'MANGA', 'BOOK'))" in SCHEMA
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
