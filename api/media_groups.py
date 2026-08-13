"""Medium groups exposed by the API, matching Anibrain's recommender split.

The manga recommender covers manhwa and manhua as well; light novels and
one-shots are first-class media of their own.
"""

from typing import Literal

MediumGroup = Literal["anime", "manga", "light_novel", "one_shot"]

MEDIUM_GROUPS: dict[str, list[str]] = {
    "anime": ["anime"],
    "manga": ["manga", "manhwa", "manhua"],
    "light_novel": ["light_novel"],
    "one_shot": ["one_shot"],
}

ALL_MEDIUMS: list[str] = [m for group in MEDIUM_GROUPS.values() for m in group]


def group_for_medium(medium: str) -> list[str]:
    """The candidate mediums for a seed title's medium (same-recommender pool)."""
    for mediums in MEDIUM_GROUPS.values():
        if medium in mediums:
            return mediums
    return [medium]
