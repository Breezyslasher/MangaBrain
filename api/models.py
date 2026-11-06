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
    # True when AniList credits an English voice actor, None when the title
    # has not been checked yet. False means "checked, none found", which is
    # NOT the same as "sub only" - see the dubbed filter in api/filters.py.
    has_english_dub: bool | None = None
    # AniList dubGroup values when more than one English dub exists, e.g.
    # ["4Kids", "Odex"] for One Piece. Usually empty.
    dub_groups: list[str] = []
    # Every language a dub is known in, lowercase ISO-639-1, from ANN. Empty
    # until the ANN pass has matched this entry; English can still be true
    # through AniList alone.
    dub_languages: list[str] = []
    # AniList's own per-entry measurement, kept apart from dub_languages
    # (ANN, matched on title) so a client can tell an exact answer from a
    # name match that resolves 31.8% of the catalog.
    measured_dub_languages: list[str] = []
    # Per-entry languages from the MAL-derived cast API, joined on id_mal.
    # Separate from the two above because a reader deciding whether to
    # trust a dub badge is entitled to know which database said so.
    mal_dub_languages: list[str] = []
    # "measured" when AniList credits an English voice actor for this entry,
    # "ann" when ANN's record supplied it, "franchise" when a related entry
    # is dubbed and this one inherited the flag, None when unknown. A client
    # showing a dub badge can qualify it.
    dub_source: str | None = None


class ScoreComponents(BaseModel):
    semantic: float
    tags: float
    # None when neither side had genres to compare. Zero would read as
    # "nothing in common", which is a different claim: 34% of imported
    # books have no genre because Open Library has no genre vocabulary,
    # not because they share nothing with the seed.
    genres: float | None
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


class DescribeItem(BaseModel):
    """One description-search result.

    similarity is the best raw query-document cosine any facet gave this
    title, and is NOT the percentage /recommend reports: display_similarity
    calibrates a blended seed-to-title score, and reusing that curve on a
    different kind of comparison would invent confidence nobody measured.
    Clients should show the order, and the facet coverage, rather than
    dressing this up as a match percentage.
    """

    media: MediaOut
    similarity: float | None = None
    facets_hit: int = 1
    facet_count: int = 1


class DescribeResponse(BaseModel):
    """facets echoes how the query was split, since a comma-separated query
    is ranked facet by facet and fused: without it a client cannot explain
    why "2/3" appears next to a result."""

    facets: list[str]
    results: list[DescribeItem]


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
    hardcover_token: str | None = None
    tmdb_api_key: str | None = None
    # The media tabs to show. Switching on a medium whose dataset is not
    # installed requests the install (api.datasets); switching one off only
    # hides the tab and deletes nothing.
    enabled_media: list[str] | None = None


class AppSettingsOut(BaseModel):
    anilist_username: str = ""
    mal_username: str = ""
    kitsu_username: str = ""
    yamtrack_url: str = ""
    # The token itself is never returned, only whether one is configured.
    yamtrack_token_set: bool = False
    hardcover_token_set: bool = False
    tmdb_api_key_set: bool = False
    # Never empty. Until someone chooses, every medium whose data is
    # present - so an install without books does not show an empty tab.
    enabled_media: list[str] = []


class DatasetInstallOut(BaseModel):
    """Where an install stands, as the worker last reported it."""

    # queued | running | waiting | done | failed
    state: str
    # downloading | merging | indexing, while running
    phase: str | None = None
    bytes: int | None = None
    total: int | None = None
    message: str = ""
    updated_at: str | None = None
    # When a waiting install is next tried.
    next_try: str | None = None


class DatasetOut(BaseModel):
    """One dataset an install can hold, and the media it provides."""

    key: str
    label: str
    description: str
    media: list[str]
    download_hint: str = ""
    installable: bool
    installed: bool
    items: int
    embedded: int
    install: DatasetInstallOut | None = None
    # Whether there is anything to install yet. None when it cannot be
    # known (GitHub unreachable) or does not apply.
    release_available: bool | None = None


class DatasetsOut(BaseModel):
    datasets: list[DatasetOut]


class DubLanguageOut(BaseModel):
    """One dub language the catalog has, with how many titles carry it."""

    code: str
    titles: int


class DubLanguagesOut(BaseModel):
    """The dub languages actually present, for building a picker from data.

    Returned sorted by title count. The UI had nine hardcoded options while
    the database held 29, which is what a hardcoded list does as coverage
    grows - so the set comes from here and only the display names stay in
    the client, where an unknown code falls back to showing the code.
    """

    languages: list[DubLanguageOut] = []


class DubCoverageOut(BaseModel):
    """How far the English dub backfill has got, over ANIME rows only."""

    total: int
    # Rows with a non-null english_va_count: checked at least once.
    checked: int
    dubbed: int
    # Checked but with no cast recorded upstream at all, which is most of the
    # least-popular tail (music videos, kids shorts, educational OVAs) and is
    # not evidence either way about a dub.
    without_cast: int
    # Rows AniList will never answer for: deleted upstream or reclassified out
    # of ANIME, so their retries are exhausted and english_va_count stays NULL
    # for good. Counted as settled rather than pending, or a single one holds
    # `complete` false forever.
    unreachable: int = 0
    # False means the dubbed filter is still hiding unchecked titles.
    complete: bool
