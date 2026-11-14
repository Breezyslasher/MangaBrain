"""Profiles: separate accounts and lists for each person using one server.

MangaBrain was single-user: one AniList, MAL and Kitsu username, one
Yamtrack and Hardcover connection, and one synced copy of each list, shared
by every browser and phone. A profile gives each person their own.

Each request says which profile it is for in an X-Profile header (or a
?profile= parameter, for a link). The middleware in api.main puts it in a
context variable, so the settings and list code below reads it without
every route passing it along. No header is the default profile, which is
exactly the single-user setup as it was: same setting keys, same list
names, so an existing install loses nothing.

What is per profile:
  - account names and tokens (AniList, MAL, Kitsu, Yamtrack, Hardcover),
    stored under "profile:<name>:<key>" in app_settings;
  - named lists (Kitsu, Yamtrack, Hardcover and hand-posted ones), stored
    as "<name>~<list>". A list name cannot contain "~", so a profile's lists
    never collide with another's or with the default's.
AniList and MAL lists are already stored per username, so two people with
different accounts already have separate lists.

What stays shared: the catalogue, the media tabs that are installed, and
the TMDB key, which only downloads public catalogue data.

This is not authentication. Anyone who can reach the server can pick any
profile, as anyone could already read the one shared setup; AUTH_TOKEN
still guards the whole server.
"""

import json
import re
from contextvars import ContextVar

DEFAULT = ""
DEFAULT_LABEL = "default"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LIST_SEPARATOR = "~"
REGISTRY_KEY = "profiles"

_current: ContextVar[str] = ContextVar("mangabrain_profile", default=DEFAULT)


def normalise(raw: str | None) -> str:
    """The profile a request names; DEFAULT for none or "default".
    Raises ValueError for a name that is not allowed."""
    name = (raw or "").strip().lower()
    if name in ("", DEFAULT_LABEL):
        return DEFAULT
    if not NAME_RE.match(name):
        raise ValueError("a profile name is 1-32 chars of a-z, 0-9, hyphen and underscore")
    return name


def current() -> str:
    return _current.get()


def use(name: str):
    """Make `name` the current profile; returns a token for reset()."""
    return _current.set(name)


def reset(token) -> None:
    _current.reset(token)


def label(name: str | None = None) -> str:
    name = current() if name is None else name
    return name or DEFAULT_LABEL


def setting_key(key: str, name: str | None = None) -> str:
    name = current() if name is None else name
    return key if name == DEFAULT else f"profile:{name}:{key}"


def list_name(name: str, profile: str | None = None) -> str:
    """The stored name of a named list for the current profile."""
    profile = current() if profile is None else profile
    return name if profile == DEFAULT else f"{profile}{LIST_SEPARATOR}{name}"


def list_names(names: list[str] | None) -> list[str]:
    return [list_name(n) for n in names or []]


def shown_list_name(stored: str) -> str:
    """A stored list name as the client knows it, without its profile."""
    return stored.split(LIST_SEPARATOR, 1)[1] if LIST_SEPARATOR in stored else stored


def registered(conn) -> list[str]:
    """Every profile made, the default first."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (REGISTRY_KEY,)).fetchone()
    names: list[str] = []
    if row is not None:
        value = row["value"] if isinstance(row, dict) else row[0]
        try:
            names = [n for n in json.loads(value) if isinstance(n, str) and NAME_RE.match(n)]
        except (TypeError, ValueError):
            names = []
    return [DEFAULT_LABEL, *sorted(set(names))]


def save_registry(conn, names: list[str]) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (REGISTRY_KEY, json.dumps(sorted(set(names)))),
    )
