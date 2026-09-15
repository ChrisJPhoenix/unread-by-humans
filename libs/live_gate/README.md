# live_gate

The pure decision + narrow-fingerprint + green-cache for **gated live
tests** — see `design/testing.md` §"Gated live tests" for the full
contract. Some behaviour can only be checked against a real external
service (cost/creds/non-determinism); a gated live test runs it
automatically but throttled, keyed by a narrow `covered-paths` fingerprint
so it retriggers only when relevant code changes and never bit-rots
unnoticed.

This library is only the pieces that don't touch a real service:

- `TTL_SECONDS` — the 4-hour freshness window.
- `fingerprint_of_pairs(pairs)` — pure hex-sha256 digest over
  `[path, content]` pairs, order-independent and content-sensitive.
- `covered_fingerprint(paths, repo_root)` — disk wrapper: expands each
  declared covered path (file or directory, recursively) to its current
  contents and folds them through `fingerprint_of_pairs`. A missing path
  contributes a sentinel so its appearance/disappearance still busts the
  cache.
- `gate_decision(cache_entry, now, current_fingerprint, creds_present,
  ttl_seconds=TTL_SECONDS)` — pure RUN / SKIP-as-pass / FAIL-no-creds
  decision, fail-safe toward RUN on any ambiguous input (missing/corrupt
  cache, no fingerprint stored, clock skew). Creds-absent returns
  `{"action": "fail"}` (surfaced red) by default, because creds must
  always be present on the machine running the live tier; automated/
  keyless contexts suppress the whole tier via the `--no-live-tests`
  flag, or opt into skip-as-pass on no-creds via `skip_if_keyless`
  (wired through `bin/dev test`'s `--skip-live-tests-if-keyless`, which
  `bin/green` now passes by default so a keyless machine's gated tier
  reports a skip instead of a fail).
- `default_cache_path(repo_root)` — `<repo_root>/tmp/live_gate_cache.json`.
- `read_cache(cache_path, test_id)` / `write_green(cache_path, test_id,
  now, fingerprint)` — the on-disk green-cache, keyed by test id. Never
  committed; a cleared cache just means "run it" (fail-safe).

`gate.py` also carries three `_probe_json` test-support wrappers used only
by `test_primary.txt`, since the plan-file harness calls flat functions
with scalar/string args and compares string returns.

**Not in this library (later steps):** wiring this gate into
`bin/dev test` / `bin/green`'s output (the `Gated (fresh, …): <id>` /
`Gated (stale/changed): running <id>` lines), and the actual live
`run()` implementations that make the real API/service calls.
