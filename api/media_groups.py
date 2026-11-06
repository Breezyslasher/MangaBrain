"""Medium groups exposed by the API, matching Anibrain's recommender split.

The manga recommender covers manhwa and manhua as well; light novels and
one-shots are first-class media of their own.

Books are a group of their own rather than a light-novel variant. They
share the embedding space with everything else - that is what makes
cross-media similarity free, and the reason the catalogue is one table
with a discriminator rather than one table per medium - but a reader
asking for books does not want light novels mixed in by default, any
more than an anime search wants manga.
"""

from typing import Literal

MediumGroup = Literal["anime", "manga", "light_novel", "one_shot", "book"]

MEDIUM_GROUPS: dict[str, list[str]] = {
    "anime": ["anime"],
    "manga": ["manga", "manhwa", "manhua"],
    "light_novel": ["light_novel"],
    "one_shot": ["one_shot"],
    "book": ["book"],
}

ALL_MEDIUMS: list[str] = [m for group in MEDIUM_GROUPS.values() for m in group]


def group_for_medium(medium: str) -> list[str]:
    """The candidate mediums for a seed title's medium (same-recommender pool)."""
    for mediums in MEDIUM_GROUPS.values():
        if medium in mediums:
            return mediums
    return [medium]
