"""Named, reproducible RNG registry.

Every library that uses randomness obtains its RNG from this
registry by name. The test harness calls `pin(master_seed)` at
the start of each plan so every library sees a deterministic RNG
for the duration of the plan.

Named RNGs are derived from the master seed + a stable hash of
the name, so changing the master seed reshuffles everything and
changing a name only reshuffles that one stream.
"""
from libs.seed_registry.seed_registry import (
    get_rng,
    pin,
    unpin,
    is_pinned,
    current_master,
    sample_uint64,
    DEFAULT_MASTER,
)

__all__ = [
    "get_rng",
    "pin",
    "unpin",
    "is_pinned",
    "current_master",
    "sample_uint64",
    "DEFAULT_MASTER",
]
