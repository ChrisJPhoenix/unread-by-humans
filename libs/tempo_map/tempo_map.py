"""tempo_map.py — convert beat positions to seconds under a tempo map.

A tempo map is an ordered list of (beat_position, bpm) segments. Each
segment runs at its bpm until the next segment's beat; the final segment
extends indefinitely. One quarter-note beat lasts 60/bpm seconds.
"""
from __future__ import annotations

from collections.abc import Iterable

_DEFAULT_BPM = 120.0
_BEAT_EPSILON = 1e-9


class TempoConflictError(ValueError):
    """Raised when two different bpm values are set at the same beat."""


def build_tempo_map(changes: Iterable) -> list[tuple[float, float]]:
    """Build an ordered tempo map from (beat, bpm) change events.

    Args:
        changes: iterable of (beat, bpm) pairs (bpm > 0). Rows of a
            NumPy 2-D array are also accepted (each row is a (beat, bpm) pair).

    Returns:
        Ordered list of (beat, bpm) segments, sorted by beat, with duplicate
        beats collapsed. A (0.0, 120.0) default segment is prepended when no
        change sits at beat 0, so the map always covers the song from beat 0.

    Raises:
        TempoConflictError: two different bpm values at the same beat.
    """
    rows = sorted(
        ((float(beat), float(bpm)) for beat, bpm in changes),
        key=lambda row: row[0],
    )
    segments: list[tuple[float, float]] = []
    for beat, bpm in rows:
        if segments and abs(segments[-1][0] - beat) <= _BEAT_EPSILON:
            if abs(segments[-1][1] - bpm) > _BEAT_EPSILON:
                raise TempoConflictError(
                    f"conflicting tempo at beat {beat:g}: "
                    f"{segments[-1][1]:g} vs {bpm:g} BPM"
                )
            continue  # same beat, same bpm — collapse to one segment
        segments.append((beat, bpm))
    if not segments or segments[0][0] > _BEAT_EPSILON:
        segments.insert(0, (0.0, _DEFAULT_BPM))
    return segments


def beats_to_seconds(beat: float, tempo_map: list) -> float:
    """Return the wall-clock seconds from beat 0 to `beat` under `tempo_map`.

    Sums each tempo segment the interval [0, beat) passes through, where one
    quarter-note beat lasts 60/bpm seconds for that segment's bpm.
    """
    beat = float(beat)
    segments = [(float(b), float(bpm)) for b, bpm in tempo_map]
    total = 0.0
    for i, (seg_beat, seg_bpm) in enumerate(segments):
        if seg_beat >= beat:
            break
        seg_end = segments[i + 1][0] if i + 1 < len(segments) else beat
        seg_end = min(seg_end, beat)
        total += (seg_end - seg_beat) * (60.0 / seg_bpm)
    return total


def segment_seconds(start_beat: float, end_beat: float, tempo_map: list) -> float:
    """Return the seconds elapsed between two beat positions under `tempo_map`.

    Used to compute a note's duration in seconds (correct even when the note
    straddles a tempo change).
    """
    return beats_to_seconds(end_beat, tempo_map) - beats_to_seconds(start_beat, tempo_map)
