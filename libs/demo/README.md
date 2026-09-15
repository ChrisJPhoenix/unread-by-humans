# demo

Throwaway library that exists only so the test harness has a
small, real surface to exercise in its self-test:

- `identity(x)` — returns x. Used to test mismatch detection.
- `add(a, b)` — used for arg-passing tests.
- `always_raises()` — used for `expect raises = ...`.
- `labeled_count()` — returns `"count: 42"`; used to test harness `normalize:` masking.
- `future_thing()` — `@stub`-decorated; used to test the
  stub-boundary behaviour of the harness.

When a real Phase-1 library (e.g. `extract`) plays the same
role, this library can be deleted.
