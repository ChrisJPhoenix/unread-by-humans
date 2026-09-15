# test_harness

The Phase-0 test runner. Reads human-readable plan files
co-located with each library, executes the calls they describe,
and compares actual to expected.

## Modules

- `parser.py` — plan-file parser. Produces a list of `Step`s.
- `stub.py` — the `@stub` decorator and `is_stub` predicate.
- `harness.py` — execution, mismatch detection, stub-boundary
  handling, learn-mode rewriting, plan discovery, and the
  stub-coverage check.

## Self-test

`test_primary.txt` exercises this library by feeding inline
plan strings to `run_text` and asserting on the resulting
`PlanResult`. It covers:

- stanza parsing and blank-line separation
- success / failure / error result statuses
- `expect raises`
- parse-error reporting (no crashes on malformed plans)
- stub-boundary halting and `Stubbed:` line emission
- plan discovery
- `@stub` ↔ `test_stub_<fn>.txt` coverage check

See `design/testing.md` for the plan-file format spec.
