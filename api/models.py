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


class MalListStatus(BaseModel):
    list_type: str
    entry_count: int
    fetched_at: datetime


class MalStatus(BaseModel):
    username: str
    lists: list[MalListStatus]
