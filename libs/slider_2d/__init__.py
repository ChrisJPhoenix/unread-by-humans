"""2D slider library — re-exports the public API."""

from .curve import Curve, sample, sample_many, to_dict, from_dict
from .automation_send import outgoing_value, automation_phase, send_key, should_send

__all__ = [
    "Curve",
    "sample",
    "sample_many",
    "to_dict",
    "from_dict",
    "outgoing_value",
    "automation_phase",
    "send_key",
    "should_send",
]
