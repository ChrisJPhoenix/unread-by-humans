"""tempo_map — beat→seconds conversion under a tempo map."""
from libs.tempo_map.tempo_map import (
    build_tempo_map,
    beats_to_seconds,
    segment_seconds,
    TempoConflictError,
)

__all__ = [
    "build_tempo_map",
    "beats_to_seconds",
    "segment_seconds",
    "TempoConflictError",
]
