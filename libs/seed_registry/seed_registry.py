"""Implementation of the named RNG registry.

The registry is a process-wide singleton kept in module globals.
`pin(seed)` installs a master seed and clears any previously
handed-out RNGs so that a fresh `get_rng(name)` produces the same
stream that a fresh process would see.

Seed derivation: we mix the master seed with a stable 64-bit hash
of the name. The hash is a deterministic FNV-1a over the UTF-8
bytes of the name, *not* Python's built-in `hash()` — that one is
salted per-process and would defeat reproducibility.
"""
import hashlib

import numpy as np

DEFAULT_MASTER = 0xC0FFEE  # arbitrary but stable

_master: int | None = None
_cache: dict[str, np.random.Generator] = {}


def pin(master_seed: int = DEFAULT_MASTER) -> None:
    """Install `master_seed` and discard any cached RNGs."""
    global _master
    _master = int(master_seed)
    _cache.clear()


def unpin() -> None:
    """Forget the master seed. `get_rng` will raise until pinned again."""
    global _master
    _master = None
    _cache.clear()


def is_pinned() -> bool:
    return _master is not None


def current_master() -> int | None:
    return _master


def get_rng(name: str) -> np.random.Generator:
    """Return a deterministic RNG for `name`.

    The same name returns the same generator instance for the
    lifetime of a `pin()` — handing it out twice would let two
    callers consume each other's stream by accident, but since
    each name is owned by exactly one library/use-site that is
    the intended contract.
    """
    if _master is None:
        raise RuntimeError(
            "seed_registry is not pinned; call pin(seed) before get_rng()"
        )
    if name in _cache:
        return _cache[name]
    derived = _derive_seed(_master, name)
    rng = np.random.default_rng(derived)
    _cache[name] = rng
    return rng


def sample_uint64(name: str) -> int:
    """Return one uint64 sample from the named RNG.

    Exposed for tests: consumes one value from the stream, which
    lets a plan file verify reproducibility by comparing scalar
    integers across re-pins.
    """
    rng = get_rng(name)
    return int(rng.integers(0, 1 << 63, dtype=np.uint64))


def _derive_seed(master: int, name: str) -> int:
    """Mix master + name into a 64-bit seed.

    SHA-256 over `f"{master}:{name}"` truncated to 64 bits. This
    is stable across Python versions and platforms; Python's
    built-in `hash()` is salted and would change run-to-run.
    """
    payload = f"{master}:{name}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big")
