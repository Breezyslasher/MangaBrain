"""Pydantic response models for every API endpoint."""

from datetime import datetime

from pydantic import BaseModel


class MediaOut(BaseModel):
    id: int
    id_mal: int | None = None
    medium: str
    title: str | None = None
    title_english: str | None = None
    title_native: str | None = None
    description: str | None = None
    genres: list[str] = []
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
    # Display-only audience-size fields. These are never inputs to scoring.
    popularity: int | None = None
    favourites: int | None = None


class ScoreComponents(BaseModel):
    semantic: float
    tags: float
    genres: float


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


class UserListStatus(BaseModel):
    list_type: str
    entry_count: int
    fetched_at: datetime


class UserListsStatus(BaseModel):
    username: str
    lists: list[UserListStatus]


class TagsResponse(BaseModel):
    tags: list[str]


class ExclusionListIn(BaseModel):
    """A generic exclusion list: AniList ids match directly; MAL ids are
    typed because MAL anime and manga ids are separate id spaces."""

    anilist_ids: list[int] = []
    mal_anime_ids: list[int] = []
    mal_manga_ids: list[int] = []


class ExclusionStatus(BaseModel):
    name: str
    entry_count: int
    updated_at: datetime
    skipped: int | None = None


class AppSettingsIn(BaseModel):
    """Partial update: omitted fields stay unchanged, empty strings clear."""

    anilist_username: str | None = None
    mal_username: str | None = None
    yamtrack_url: str | None = None
    yamtrack_token: str | None = None


class AppSettingsOut(BaseModel):
    anilist_username: str = ""
    mal_username: str = ""
    yamtrack_url: str = ""
    # The token itself is never returned, only whether one is configured.
    yamtrack_token_set: bool = False
