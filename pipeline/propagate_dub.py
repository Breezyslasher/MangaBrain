"""Infer an English dub across a franchise from a measured sibling.

AniList's dub data is reliable per entry and wrong in one specific way, and
the ANN cross-check measured which: of the AniList-silent titles that resolved
exactly on ANN, 0 of 7 actually had an English cast, so a silence about a
standalone entry is correct. The false negatives were the side entries -
BEASTARS FINAL SEASON Part 2 records 25 characters and no English voice actor
on AniList, while ANN credits 18 English cast entries to the series it belongs
to. The cast simply was not re-entered on the season's own AniList page.

So a dub is propagated across a franchise: if any anime in a connected group
of entries is known to be English-dubbed, the rest are marked as inferred.
Neither direct source is written, so "AniList credits an English voice actor
for this entry", "ANN records an English cast for it" and "a relative of this
entry is dubbed" stay distinguishable in the database and in the API.

The walk roots on either direct source. Rooting on AniList alone made this a
no-op anywhere AniList had not been walked yet, and either source can be the
one that is missing: a snapshot may be taken while a pass is still partway
through, and the two passes cover different titles - ANN only reaches what it
can match by name.

Which relations count is the whole risk. Only relations that imply the same
production in the same language are followed: sequels, prequels, parents and
side stories. Deliberately excluded:

  SUMMARY, COMPILATION  a recap of a dubbed series is frequently not dubbed
  ALTERNATIVE           a different version, often a different production
  SPIN_OFF              a different show sharing a setting
  ADAPTATION, SOURCE    the manga, which has no dub at all
  CHARACTER, OTHER      no production relationship

Usage:
    python -m pipeline.propagate_dub
    python -m pipeline.propagate_dub --max-depth 2 --dry-run
"""

import argparse

import psycopg

from api.config import settings

# Relations that imply the same production line. Everything else is excluded
# on purpose; see the module docstring.
PROPAGATE_RELATIONS = ("SEQUEL", "PREQUEL", "PARENT", "SIDE_STORY")

DEFAULT_MAX_DEPTH = 3

# Walks outward from every anime with a measured English cast, across the
# allowed relation types in both directions, to a bounded depth. Depth is
# capped because a franchise component can chain a long way and a dub becomes
# a weaker claim with every hop.
REACHABLE_SQL = """
WITH RECURSIVE edges AS (
    SELECT r.media_id AS a, r.related_id AS b
    FROM media_relations r
    WHERE r.relation_type = ANY(%(types)s)
    UNION
    SELECT r.related_id, r.media_id
    FROM media_relations r
    WHERE r.relation_type = ANY(%(types)s)
),
walk (node, depth) AS (
    SELECT m.id, 0
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND (m.english_va_count > 0 OR 'en' = ANY(m.dub_languages))
    UNION
    SELECT e.b, w.depth + 1
    FROM walk w
    JOIN edges e ON e.a = w.node
    WHERE w.depth < %(max_depth)s
)
SELECT DISTINCT w.node
FROM walk w
JOIN media m ON m.id = w.node
WHERE m.media_type = 'ANIME'
  AND COALESCE(m.english_va_count, 0) = 0
  AND NOT ('en' = ANY(m.dub_languages))
"""

MARK_SQL = """
    UPDATE media
    SET english_dub_inferred = TRUE
    WHERE id = ANY(%(ids)s)
      AND COALESCE(english_va_count, 0) = 0
      AND NOT ('en' = ANY(dub_languages))
      AND english_dub_inferred = FALSE
"""

# A previous pass may have inferred a dub for an entry whose franchise no
# longer supports it (a relation was removed, or the sibling's measured cast
# went away), so the flag is cleared before being reapplied rather than only
# ever accumulating.
CLEAR_SQL = """
    UPDATE media
    SET english_dub_inferred = FALSE
    WHERE english_dub_inferred = TRUE
      AND NOT (id = ANY(%(ids)s))
"""


def propagate(
    conn: psycopg.Connection, max_depth: int = DEFAULT_MAX_DEPTH, dry_run: bool = False
) -> tuple[int, int]:
    """Mark anime reachable from a measured dub. Returns (marked, cleared)."""
    rows = conn.execute(
        REACHABLE_SQL, {"types": list(PROPAGATE_RELATIONS), "max_depth": max_depth}
    ).fetchall()
    ids = [row[0] for row in rows]
    print(f"[propagate] {len(ids)} anime reachable from a measured English cast", flush=True)
    if dry_run:
        return len(ids), 0
    marked = conn.execute(MARK_SQL, {"ids": ids}).rowcount
    cleared = conn.execute(CLEAR_SQL, {"ids": ids}).rowcount
    conn.commit()
    print(
        f"[propagate] marked {marked} inferred, cleared {cleared} no longer supported",
        flush=True,
    )
    return marked, cleared


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer English dubs across franchises")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--dry-run", action="store_true", help="count without writing")
    args = parser.parse_args()
    with psycopg.connect(settings.database_url) as conn:
        propagate(conn, max_depth=args.max_depth, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
