"""Fritsch–Carlson monotone cubic Hermite spline math."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Segment:
    """One cubic Hermite segment between two consecutive control points."""

    x0: float
    x1: float
    y0: float
    y1: float
    m0: float  # tangent at x0 (in y/x units)
    m1: float  # tangent at x1 (in y/x units)


def build_segments(points: list[tuple[float, float]]) -> list[Segment]:
    """Build Fritsch–Carlson monotone Hermite segments from sorted control points.

    Args:
        points: Sorted list of (x, y) pairs with no internal adjacencies
                (caller splits at adjacency boundaries before calling).
                Must have at least 2 points.

    Returns:
        List of Segment objects, one per consecutive pair of points.

    Raises:
        ValueError: If fewer than 2 points are provided.
    """
    if len(points) < 2:
        raise ValueError(
            f"build_segments requires at least 2 points, got {len(points)}"
        )

    n = len(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    # Step 1: Secant slopes between consecutive points.
    secants = [
        _secant_slope(xs[i], ys[i], xs[i + 1], ys[i + 1]) for i in range(n - 1)
    ]

    # Step 2: Initial tangents — endpoints use secant, interior use average.
    tangents = _initial_tangents(secants, n)

    # Step 3: Fritsch–Carlson monotonicity fixup.
    _apply_monotonicity_fixup(tangents, secants, n)

    return [
        Segment(
            x0=xs[i],
            x1=xs[i + 1],
            y0=ys[i],
            y1=ys[i + 1],
            m0=tangents[i],
            m1=tangents[i + 1],
        )
        for i in range(n - 1)
    ]


def evaluate(segments: list[Segment], t: float) -> float:
    """Evaluate the spline at parameter t ∈ [0, 1].

    Finds the segment containing t and applies cubic Hermite evaluation.
    Returns the y value of the rightmost segment endpoint if t is beyond all segments.

    Args:
        segments: List of Segment objects from build_segments.
        t: The x-parameter at which to evaluate, typically in [0, 1].

    Returns:
        The interpolated y value at t.
    """
    if not segments:
        raise ValueError("evaluate requires at least one segment")

    seg = _find_segment(segments, t)
    return _hermite_evaluate(seg, t)


def _secant_slope(x0: float, y0: float, x1: float, y1: float) -> float:
    """Compute the secant slope between two points."""
    return (y1 - y0) / (x1 - x0)


def _initial_tangents(secants: list[float], n: int) -> list[float]:
    """Compute initial tangent estimates before monotonicity fixup."""
    tangents = [0.0] * n
    tangents[0] = secants[0]
    tangents[n - 1] = secants[-1]
    for i in range(1, n - 1):
        tangents[i] = (secants[i - 1] + secants[i]) / 2.0
    return tangents


def _apply_monotonicity_fixup(
    tangents: list[float], secants: list[float], n: int
) -> None:
    """Apply Fritsch–Carlson monotonicity constraints in place."""
    for i in range(n - 1):
        delta = secants[i]
        if delta == 0.0:
            tangents[i] = 0.0
            tangents[i + 1] = 0.0
        else:
            alpha = tangents[i] / delta
            beta = tangents[i + 1] / delta
            magnitude_squared = alpha * alpha + beta * beta
            if magnitude_squared > 9.0:
                scale = 3.0 / math.sqrt(magnitude_squared)
                tangents[i] *= scale
                tangents[i + 1] *= scale


def _find_segment(segments: list[Segment], t: float) -> Segment:
    """Find the segment whose x-interval contains t."""
    for seg in segments:
        if t <= seg.x1:
            return seg
    return segments[-1]


def _hermite_evaluate(seg: Segment, t: float) -> float:
    """Evaluate cubic Hermite interpolation within a segment at global x=t."""
    h = seg.x1 - seg.x0
    if h == 0.0:
        return seg.y1
    u = (t - seg.x0) / h
    u2 = u * u
    u3 = u2 * u
    h00 = 2 * u3 - 3 * u2 + 1
    h10 = u3 - 2 * u2 + u
    h01 = -2 * u3 + 3 * u2
    h11 = u3 - u2
    return h00 * seg.y0 + h10 * h * seg.m0 + h01 * seg.y1 + h11 * h * seg.m1
