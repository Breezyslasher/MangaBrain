"""Content-based similarity scoring.

The ranking function takes exactly three content signals: semantic cosine
similarity over synopsis embeddings, cosine similarity over rank-weighted
tag vectors, and Jaccard similarity over genre sets. Nothing here may read
any audience-size signal; tests/test_scoring.py asserts this against the
module source, so those signals cannot even be named in this file.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_TAG_RANK = 0.5


@dataclass(frozen=True)
class Weights:
    """Slider weights for the three similarity components."""

    semantic: float = 0.5
    tags: float = 0.3
    genres: float = 0.2

    def normalized(self) -> "Weights":
        total = self.semantic + self.tags + self.genres
        if total <= 0:
            return Weights()
        return Weights(self.semantic / total, self.tags / total, self.genres / total)


def genre_similarity(genres_a: Iterable[str], genres_b: Iterable[str]) -> float:
    """Jaccard similarity over genre sets."""
    set_a, set_b = set(genres_a), set(genres_b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def tag_similarity(tags_a: Mapping[str, float], tags_b: Mapping[str, float]) -> float:
    """Cosine similarity over sparse tag vectors weighted by AniList tag rank."""
    if not tags_a or not tags_b:
        return 0.0
    dot = sum(tags_a[name] * tags_b[name] for name in tags_a.keys() & tags_b.keys())
    norm_a = math.sqrt(sum(v * v for v in tags_a.values()))
    norm_b = math.sqrt(sum(v * v for v in tags_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def tag_weight_map(tags: Iterable[Mapping]) -> dict[str, float]:
    """Turn AniList tag dicts into a name -> weight map (rank scaled to 0..1)."""
    weights: dict[str, float] = {}
    for tag in tags or []:
        name = tag.get("name")
        if not name:
            continue
        rank = tag.get("rank")
        weights[name] = (rank / 100.0) if rank else DEFAULT_TAG_RANK
    return weights


def final_score(
    semantic: float, tag_sim: float, genre_sim: float, weights: Weights = Weights()
) -> float:
    """Weighted blend of the three content similarities, each in [0, 1]."""
    w = weights.normalized()
    semantic = min(max(semantic, 0.0), 1.0)
    return w.semantic * semantic + w.tags * tag_sim + w.genres * genre_sim
