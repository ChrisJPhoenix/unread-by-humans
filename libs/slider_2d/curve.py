"""Curve dataclass and associated operations for the 2D slider library.

A Curve holds a sorted list of (x, y) control points where x ∈ [0, 1] and
y ∈ [value_min, value_max].  The two endpoint points at x=0 and x=1 are
always present and cannot be removed.

MIN_X_GAP is the minimum allowed separation between any two x values in
storage coordinates.  It corresponds to 1 pixel at a 1000-wide edit display
(1 / 999 ≈ 0.001001).  The actual edit popup enforces a coarser pixel-snapped
gap; this constant exists to keep curve.py self-contained and to guard against
degeneracy in to_dict / from_dict round-trips.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np



@dataclass
class Curve:
    """Sorted control-point sequence for a 2D automation curve.

    MIN_X_GAP is the minimum allowed x separation between any two stored points.
    It corresponds to 1 pixel at a 1000-wide edit display (1 / 999 ≈ 0.001001).
    The actual edit popup enforces a coarser pixel-snapped gap at interaction
    time; this constant guards against degeneracy in to_dict / from_dict
    round-trips and insert() validation.

    Attributes:
        value_min: Numeric minimum (bottom-left frame label value).
        value_max: Numeric maximum (top-left frame label value).
        value_min_label: Display string for the bottom-left axis label.
        value_max_label: Display string for the top-left axis label.
        time_left_label: Display string for the far-left x label (may be "").
        time_right_label: Display string for the far-right x label (may be "").
        initial_left_y: y of the x=0 endpoint at construction time.
        initial_right_y: y of the x=1 endpoint at construction time.
        points: Sorted [(x, y), ...] with x ∈ [0, 1], y ∈ [value_min, value_max].
    """

    MIN_X_GAP: ClassVar[float] = 1 / 999

    value_min: float
    value_max: float
    value_min_label: str
    value_max_label: str
    time_left_label: str
    time_right_label: str
    initial_left_y: float
    initial_right_y: float
    points: list[tuple[float, float]] = field(init=False)

    def __post_init__(self) -> None:
        self.points = [
            (0.0, self.initial_left_y),
            (1.0, self.initial_right_y),
        ]

    def insert(self, x: float, y: float) -> None:
        """Insert a new control point at (x, y).

        Args:
            x: x position in (0, 1) — open interval; endpoints are not insertable.
            y: y value in [value_min, value_max].

        Raises:
            ValueError: If x is not strictly inside (0, 1), y is out of range,
                        or x is within MIN_X_GAP of an existing point's x.
        """
        if not (0.0 < x < 1.0):
            raise ValueError(
                f"insert x must be in open interval (0, 1), got {x!r}"
            )
        if not (self.value_min <= y <= self.value_max):
            raise ValueError(
                f"insert y must be in [{self.value_min}, {self.value_max}], got {y!r}"
            )
        for px, _ in self.points:
            if abs(px - x) < self.MIN_X_GAP:
                raise ValueError(
                    f"insert x={x!r} collides with existing point at x={px!r} "
                    f"(gap {abs(px - x)!r} < MIN_X_GAP {self.MIN_X_GAP!r})"
                )
        insertion_index = bisect.bisect_left([p[0] for p in self.points], x)
        self.points.insert(insertion_index, (x, y))

    def remove(self, i: int) -> None:
        """Remove the i-th control point.

        Args:
            i: Index into self.points.  Negative indices are supported.

        Raises:
            ValueError: If i refers to the x=0 or x=1 endpoint.
        """
        resolved = i if i >= 0 else len(self.points) + i
        if resolved == 0 or resolved == len(self.points) - 1:
            raise ValueError(
                f"Cannot remove endpoint at index {i} (resolved={resolved})"
            )
        del self.points[resolved]

    def reset(self) -> None:
        """Restore the curve to its as-constructed state.

        Drops all interior control points and resets the two endpoints
        to their original y values (``initial_left_y`` and
        ``initial_right_y``), producing an identical state to what
        ``__post_init__`` produced.  Any edits made since construction
        are discarded.
        """
        self.points = [
            (0.0, self.initial_left_y),
            (1.0, self.initial_right_y),
        ]

    def move(self, i: int, x: float, y: float) -> None:
        """Reposition the i-th control point.

        Endpoint moves clamp x to its fixed value (0.0 for the left endpoint,
        1.0 for the right endpoint).  No sorting is performed; the caller is
        responsible for keeping moves within the segment [left_neighbor.x,
        right_neighbor.x] so the list stays sorted.

        Args:
            i: Index into self.points.
            x: New x position.
            y: New y position.
        """
        resolved = i if i >= 0 else len(self.points) + i
        is_left_endpoint = resolved == 0
        is_right_endpoint = resolved == len(self.points) - 1

        if is_left_endpoint:
            x = 0.0
        elif is_right_endpoint:
            x = 1.0

        self.points[resolved] = (x, y)


def sample(curve: Curve, t: float) -> float:
    """Sample the curve at x=t using monotone Hermite spline interpolation.

    Args:
        curve: The Curve to sample.
        t: x-parameter in [0, 1].

    Returns:
        Interpolated y value at x=t.
    """
    from .spline import build_segments, evaluate
    return evaluate(build_segments(curve.points), t)


def sample_many(curve: Curve, ts: np.ndarray) -> np.ndarray:
    """Vectorized curve sampling.  Calls sample() for each element of ts.

    Args:
        curve: The Curve to sample.
        ts: 1-D array of x-parameters.

    Returns:
        1-D array of interpolated y values, same shape as ts.
    """
    return np.array([sample(curve, float(t)) for t in ts])


def to_dict(curve: Curve) -> dict:
    """Serialize a Curve to a JSON-serializable dict.

    Args:
        curve: The Curve to serialize.

    Returns:
        Dict with all Curve fields including points as [[x, y], ...].
    """
    return {
        "value_min": curve.value_min,
        "value_max": curve.value_max,
        "value_min_label": curve.value_min_label,
        "value_max_label": curve.value_max_label,
        "time_left_label": curve.time_left_label,
        "time_right_label": curve.time_right_label,
        "initial_left_y": curve.initial_left_y,
        "initial_right_y": curve.initial_right_y,
        "points": [[x, y] for x, y in curve.points],
    }


def from_dict(d: dict) -> Curve:
    """Reconstruct a Curve from the dict form produced by to_dict.

    Builds an empty Curve with the stored constructor args, then replays
    endpoints via move() and inserts interior points via insert().

    Args:
        d: Dict as produced by to_dict.

    Returns:
        Reconstructed Curve.
    """
    curve = Curve(
        value_min=d["value_min"],
        value_max=d["value_max"],
        value_min_label=d["value_min_label"],
        value_max_label=d["value_max_label"],
        time_left_label=d["time_left_label"],
        time_right_label=d["time_right_label"],
        initial_left_y=d["initial_left_y"],
        initial_right_y=d["initial_right_y"],
    )
    saved_points = d["points"]
    # Replay endpoints (index 0 and -1 in saved list).
    if saved_points:
        lx, ly = saved_points[0]
        curve.move(0, lx, ly)
        rx, ry = saved_points[-1]
        curve.move(-1, rx, ry)
    # Insert interior points in order.
    for x, y in saved_points[1:-1]:
        curve.insert(x, y)
    return curve
