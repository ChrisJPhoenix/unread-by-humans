# seed_registry

A single process-wide registry that hands out named,
reproducible NumPy RNGs.

## Why

Determinism is a Phase-0 invariant. Every library that uses
randomness (the random-window stub extractor in Phase 1, NN
weight init in Phase 4, dropout in training, the
deterministic-CC tie-breakers in Phase 6) must obtain its RNG
from a single point so the test harness can pin them all at
once.

## API

```python
from libs import seed_registry

seed_registry.pin(42)         # called by the harness, not by libs
rng = seed_registry.get_rng("binarize.otsu")
rng.uniform()                  # numpy.random.Generator API
```

- `pin(seed)` installs a master seed and clears any previously
  handed-out RNGs.
- `get_rng(name)` returns a deterministic generator for `name`.
  Same name, same master → same stream. Different names are
  independent.
- `unpin()` reverts to the unpinned state (mostly for tests).

## Naming convention

Use dotted names: `"<library>.<purpose>"`, e.g.
`"extract.random_windows"`, `"nn.init.dense_1"`. The harness
report uses these names verbatim in trace output.
