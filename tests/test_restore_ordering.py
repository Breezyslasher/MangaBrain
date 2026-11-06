"""Restoring a published snapshot onto a database that already has a schema.

The model-bench job failed on it: "cannot drop constraint media_pkey on
table public.media because other objects depend on it ...
dub_backfill_state_media_id_fkey depends on index public.media_pkey".

pg_restore --clean has no CASCADE, so a single local table holding a
foreign key into media stopped the entire restore - the drop failed,
CREATE TABLE then failed as "already exists", and the primary key as
"multiple primary keys". Four errors, ignored, then a non-zero exit and
no catalogue.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESTORE = (ROOT / "pipeline" / "restore.py").read_text()
SCHEMA = (ROOT / "db" / "schema.sql").read_text()


def test_the_schema_is_applied_after_the_restore_not_before():
    """ensure_schema() creates the very tables whose foreign keys block
    the restore. Running it first is what caused the failure."""
    restore_at = RESTORE.index("drop_dependent_constraints()\n    if args.file:")
    schema_at = RESTORE.index("ensure_schema()", restore_at)
    assert restore_at < schema_at


def test_an_absent_media_table_counts_as_an_empty_catalogue():
    """A missing table is the emptiest a catalogue can be. Treating it
    as an error is what forced ensure_schema() to run first."""
    assert "except psycopg.errors.UndefinedTable:" in RESTORE
    assert "return True" in RESTORE


def test_only_foreign_keys_from_outside_the_snapshot_are_dropped():
    """media_relations and embeddings are in the snapshot and are
    replaced wholesale. Local tables - user lists, backfill state - must
    survive a restore, so only their keys are dropped, not their rows."""
    assert "conrelid::regclass::text <> ALL(%(tables)s)" in RESTORE
    assert "contype = 'f'" in RESTORE
    assert "DROP TABLE" not in RESTORE


def test_the_dropped_key_is_restored_by_the_schema():
    """CREATE TABLE IF NOT EXISTS cannot add a constraint to a table
    that already exists, so dropping one without re-stating it would
    leave the database weaker after every restore."""
    assert SCHEMA.count("dub_backfill_state_media_id_fkey") >= 1
    assert "ADD CONSTRAINT dub_backfill_state_media_id_fkey" in SCHEMA


def test_every_table_referencing_the_snapshot_is_accounted_for():
    """If a new local table gains a foreign key into media, the restore
    has to know - the generic query covers it, but the snapshot list
    must stay correct."""
    from pipeline.restore import SNAPSHOT_TABLES
    from pipeline.snapshot import DATA_TABLES

    assert set(SNAPSHOT_TABLES) == set(DATA_TABLES)


def test_extensions_exist_before_the_restore():
    """The snapshot's own indexes need them.

    Moving ensure_schema() after the restore took the extension
    creation with it, and the next run failed on "operator class
    public.gin_trgm_ops does not exist for access method gin" - the
    dump's trigram indexes, with pg_trgm not yet installed. One fix
    traded for another.

    Extensions before, tables after: creating the tables early is what
    the reordering exists to avoid.
    """
    extensions_at = RESTORE.index("ensure_extensions()\n    drop_dependent_constraints()")
    restore_at = RESTORE.index("restore_file(Path(args.file))")
    schema_at = RESTORE.index("ensure_schema()", restore_at)
    assert extensions_at < restore_at < schema_at


def test_only_extensions_are_created_early():
    """Not the schema: a CREATE TABLE before the restore is the bug."""
    from pipeline.restore import EXTENSIONS

    body = RESTORE[RESTORE.index("def ensure_extensions(") :]
    body = body[: body.index("\ndef ", 1)]
    assert "CREATE EXTENSION IF NOT EXISTS" in body
    assert "CREATE TABLE" not in body
    assert set(EXTENSIONS) == {"vector", "pg_trgm"}


def test_the_extensions_match_what_the_schema_declares():
    """If the schema gains an extension, a restore that predates it
    would fail on the dump's indexes."""
    declared = {
        line.split("IF NOT EXISTS")[1].strip().rstrip(";")
        for line in SCHEMA.splitlines()
        if line.startswith("CREATE EXTENSION")
    }
    from pipeline.restore import EXTENSIONS

    assert declared == set(EXTENSIONS)
