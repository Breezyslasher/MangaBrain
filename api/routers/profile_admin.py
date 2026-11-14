"""Listing, making and removing profiles (see api.profiles)."""

from fastapi import APIRouter, HTTPException

from api import profiles
from api.db import get_pool
from api.models import ProfileIn, ProfilesOut

router = APIRouter()


@router.get("/profiles", response_model=ProfilesOut)
def list_profiles() -> ProfilesOut:
    with get_pool().connection() as conn:
        return ProfilesOut(profiles=profiles.registered(conn), current=profiles.label())


@router.post("/profiles", response_model=ProfilesOut)
def make_profile(body: ProfileIn) -> ProfilesOut:
    try:
        name = profiles.normalise(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if name == profiles.DEFAULT:
        raise HTTPException(status_code=422, detail="the default profile always exists")
    with get_pool().connection() as conn:
        names = [n for n in profiles.registered(conn) if n != profiles.DEFAULT_LABEL]
        profiles.save_registry(conn, [*names, name])
        return ProfilesOut(profiles=profiles.registered(conn), current=profiles.label())


@router.delete("/profiles/{name}", response_model=ProfilesOut)
def remove_profile(name: str) -> ProfilesOut:
    """Removes the profile, its account settings and its named lists.
    AniList and MAL lists are stored per username and may be another
    profile's too, so they stay."""
    try:
        name = profiles.normalise(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if name == profiles.DEFAULT:
        raise HTTPException(status_code=422, detail="the default profile cannot be removed")
    with get_pool().connection() as conn:
        names = [n for n in profiles.registered(conn) if n != profiles.DEFAULT_LABEL]
        if name not in names:
            raise HTTPException(status_code=404, detail=f"no profile named '{name}'")
        profiles.save_registry(conn, [n for n in names if n != name])
        conn.execute("DELETE FROM app_settings WHERE key LIKE %s", (f"profile:{name}:%",))
        prefix = profiles.list_name("", name) + "%"
        conn.execute("DELETE FROM custom_exclusion_entries WHERE list_name LIKE %s", (prefix,))
        conn.execute("DELETE FROM custom_exclusion_state WHERE list_name LIKE %s", (prefix,))
        return ProfilesOut(profiles=profiles.registered(conn), current=profiles.label())
