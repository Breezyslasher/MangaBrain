"""Pydantic request and response models for every API endpoint."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MediaOut(BaseModel):
    id: int
    id_mal: int | None = None
    medium: str
    title: str | None = None
    title_english: str | None = None
    title_native: str | None = None
    description: str | None = None
    genres: list[str] = []
    # Tag names ordered by AniList rank (strongest first), for display.
    tags: list[str] = []
    format: str | None = None
    episodes: int | None = None
    chapters: int | None = None
    volumes: int | None = None
    country_of_origin: str | None = None
    start_year: int | None = None
    status: str | None = None
    average_score: int | None = None
    cover_image: str | None = None
    cover_image_large: str | None = None
    is_adult: bool = False
    # Alternate titles, so a client can confirm that a title it knows by
    # another name is this entry. Capped per row to keep a 50-result
    # /recommend response small.
    synonyms: list[str] = []
    # Display-only audience-size fields. These are never inputs to scoring.
    popularity: int | None = None
    favourites: int | None = None


class ScoreComponents(BaseModel):
    semantic: float
    tags: float
    genres: float
    # Similarity to the user's own rating-weighted taste profile; present
    # only when the optional taste weight is active.
    taste: float | None = None


class RecommendationItem(BaseModel):
    media: MediaOut
    similarity: float  # percentage, 0-100
    components: ScoreComponents


class RelatedItem(BaseModel):
    media: MediaOut
    relation_type: str


class RecommendResponse(BaseModel):
    seed: MediaOut
    # All seeds for multi-seed requests; a single-element list otherwise.
    seeds: list[MediaOut] = []
    weights: ScoreComponents
    results: list[RecommendationItem]
    related: list[RelatedItem]


class SearchResponse(BaseModel):
    results: list[MediaOut]


# Identity, not ranking: a wrong id would exclude an unrelated title and pull
# the taste profile toward something the user never read, so resolution is
# exact-match only and reports ambiguity instead of guessing.
ResolveMatchKind = Literal["mal", "title", "synonym", "ambiguous", "none"]


class ResolveItemIn(BaseModel):
    """One library title to resolve. key is echoed back untouched so the
    caller can line results up with its own records."""

    key: str
    titles: list[str] = []
    mal_id: int | None = None


class ResolveRequest(BaseModel):
    items: list[ResolveItemIn] = Field(default_factory=list, max_length=500)
    adult: bool = False


class ResolveMatch(BaseModel):
    id: int | None = None
    match: ResolveMatchKind


class ResolveResponse(BaseModel):
    results: dict[str, ResolveMatch]


class UserListStatus(BaseModel):
    list_type: str
    entry_count: int
    fetched_at: datetime


class UserListsStatus(BaseModel):
    username: str
    lists: list[UserListStatus]


class TagsResponse(BaseModel):
    tags: list[str]


# Kitsu ids are accepted on input only: they are resolved to the MAL or
# AniList ids they map to before anything is stored, because that is what
# the exclusion filter joins on.
ExclusionKind = Literal["anilist", "mal_anime", "mal_manga", "kitsu_anime", "kitsu_manga"]


class ExclusionEntryIn(BaseModel):
    """One list entry with the tracker state a client knows about.

    planned marks a plan-to-watch/plan-to-read entry, which the keep_planned
    query option can leave recommendable; score is the user's rating
    normalized to 0-100 (None = unrated) and feeds For-you sampling and the
    taste profile.
    """

    kind: ExclusionKind
    id: int
    planned: bool = False
    score: int | None = Field(default=None, ge=0, le=100)


class ExclusionListIn(BaseModel):
    """A generic exclusion list: AniList ids match directly; MAL ids are
    typed because MAL anime and manga ids are separate id spaces.

    The bare id arrays mean "on the list, status unknown": they stay
    excluded under keep_planned and sample with neutral weight. entries
    carries the same ids with the tracker status and rating attached, and
    the two may be mixed - an entry still contributes its rating to an id
    that also arrived as a bare id.
    """

    anilist_ids: list[int] = []
    mal_anime_ids: list[int] = []
    mal_manga_ids: list[int] = []
    entries: list[ExclusionEntryIn] = []


class ExclusionStatus(BaseModel):
    name: str
    entry_count: int
    updated_at: datetime
    skipped: int | None = None


class AppSettingsIn(BaseModel):
    """Partial update: omitted fields stay unchanged, empty strings clear."""

    anilist_username: str | None = None
    mal_username: str | None = None
    kitsu_username: str | None = None
    yamtrack_url: str | None = None
    yamtrack_token: str | None = None


class AppSettingsOut(BaseModel):
    anilist_username: str = ""
    mal_username: str = ""
    kitsu_username: str = ""
    yamtrack_url: str = ""
    # The token itself is never returned, only whether one is configured.
    yamtrack_token_set: bool = False
