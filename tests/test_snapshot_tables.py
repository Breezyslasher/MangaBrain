"""Snapshot table lists must track the schema.

The --all backup silently missed tables added after its USER_TABLES list was
written (exclusion lists, app settings), so restores came back with accounts
and exclusions wiped. These tests tie the lists to schema.sql: a new CREATE
TABLE must be classified into DATA_TABLES or USER_TABLES before it lands.
"""

import re
from pathlib import Path

from pipeline.snapshot import DATA_TABLES, USER_TABLES

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Tables holding account or tracker data; they must never ship in the public
# weekly dataset (built without --all). app_settings holds the Yamtrack token.
SENSITIVE_TABLES = {
    "app_settings",
    "custom_exclusion_entries",
    "custom_exclusion_state",
    "mal_list_entries",
    "anilist_list_entries",
}


def schema_tables() -> set[str]:
    sql = SCHEMA.read_text()
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))


def test_every_schema_table_is_classified_for_snapshots():
    missing = schema_tables() - set(DATA_TABLES) - set(USER_TABLES)
    assert not missing, (
        f"tables {sorted(missing)} are in schema.sql but in neither DATA_TABLES"
        " nor USER_TABLES; --all backups would silently drop them"
    )


def test_no_stale_snapshot_table_entries():
    stale = (set(DATA_TABLES) | set(USER_TABLES)) - schema_tables()
    assert not stale, f"tables {sorted(stale)} are listed for snapshots but not in schema.sql"


def test_sensitive_tables_stay_out_of_the_public_dataset():
    leaked = SENSITIVE_TABLES & set(DATA_TABLES)
    assert not leaked, f"user data tables {sorted(leaked)} would ship in the public dataset"
    assert SENSITIVE_TABLES <= set(USER_TABLES)
