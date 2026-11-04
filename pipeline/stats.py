"""Small statistics shared by the probes and the dub audit.

Extracted from probe_dub_studios, which needed it first. The audit ranks fix
classes by how many titles each would recover from a sample, and a point
estimate off eight sampled titles would let a tiny class outrank a large one
- the same failure the studio ranking used this to avoid.
"""

import math


def wilson_lower(hits: int, total: int, z: float = 1.96) -> float:
    """Lower bound of the 95% interval, so small samples cannot top a list.

    A studio seen twice with both titles dubbed has a point estimate of 100%
    and tells you nothing. The bound is what makes support and rate
    comparable in one number.
    """
    if total == 0:
        return 0.0
    p = hits / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denom)


def wilson_interval(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Both bounds, for reporting a sampled rate as a range.

    The audit extrapolates a sampled rate to a whole bucket, and a bare point
    estimate there invites exactly the error this investigation already made
    once - fitting a confident story to a number a small sample could not
    support. Reporting the range makes an under-sampled class look
    under-sampled.
    """
    if total == 0:
        return 0.0, 1.0
    p = hits / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)
