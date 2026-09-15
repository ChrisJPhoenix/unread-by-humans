"""Pure decision + narrow-fingerprint + green-cache for gated live tests.

See `design/testing.md` §"Gated live tests" and `libs/live_gate/README.md`.
"""
from libs.live_gate.gate import (
    TTL_SECONDS,
    fingerprint_of_pairs,
    covered_fingerprint,
    gate_decision,
    read_cache,
    write_green,
    default_cache_path,
)

__all__ = [
    "TTL_SECONDS",
    "fingerprint_of_pairs",
    "covered_fingerprint",
    "gate_decision",
    "read_cache",
    "write_green",
    "default_cache_path",
]
