# web/smoke/ — Domain-2 Web Smoke-Test Contract

The Domain-2 web smoke test is decomposed into per-project files discovered by
the glob `web/smoke/*.py`. Files whose basename starts with `_` (e.g.
`_harness.py`) are skipped — they are infrastructure, not project files.

## Entry point: `_harness.py`

`_harness.py` is the sole entry point. It:

1. Builds ONE shared temp root under `tmp/` (prefix `smoke_`).
2. Constructs Flask test clients and bundles them into a `SmokeContext`.
3. Discovers project files (sorted, deterministic) via the glob above.
4. Runs every project's optional `seed(ctx)` — the seed-all phase.
5. Runs every project's `run(ctx, check)` — the run-all phase.
6. Counts checks dynamically (no hardcoded total), prints
   `smoke: <passed> passed, <failed> failed`, and returns 0 iff zero failures.

## The `SmokeContext` object

| Attribute | Contents |
|---|---|
| `temp_root` | Per-run temp dir (wiped on exit) |
| `project_root` | Real repo root |
| `client` | Flask test client for `create_app(temp_root)` |
| `real_client` | Test client for `create_app(PROJECT_ROOT)` — read-only real-asset checks |

Cross-cutting fixtures seeded by `_harness.py`: `notes/hello.txt`, `sub/inner`,
and `linkdir/` with a dangling symlink.

## Project-file contract

Each project file exposes:

```
def run(ctx: SmokeContext, check) -> None: ...          # required
def seed(ctx: SmokeContext) -> None: ...                # optional
```

`check(label, condition)` prints `[PASS]` or `[FAIL]` and tallies the result.
`seed` creates fixtures only that project needs; it runs before any `run` call.

**To add a project:** drop `web/smoke/<project>.py` exposing `run(ctx, check)`
(and an optional `seed(ctx)`). Discovery picks it up automatically — no edits
to `_harness.py` or `libs/dev_test` required.

## Current project files

| File | Routes covered |
|---|---|
| `fs.py` | All `/fs/*` endpoints (read, write, mkdir, list, path-guard, dangling-symlink regression) |
| `reload.py` | `GET /` app index (contains the `/music` link), the injected reload client, the `window.__APP_ROUTE__ = null` marker on a non-app page and `= "/music"` on `/music`, and the `GET /sse-reload` nonce-based event stream (reads only the first chunk — the stream is infinite) |
| `error_surfacing.py` | `/_test_crash` (`mode=raise` JSON and HTML, `mode=abort` passthrough) and `POST /_client_error` (valid and invalid bodies), plus the injected `error_overlay.mjs` script tag |
| `music.py` | All `/music/*` routes except `/music/flac` (untested by design), through the `FakeBackend` seam |

Only these four ship in this repository; the main tree this was carved from
has more per-app smoke files, discovered the same way.

## Analogy to Domain 3

This mirrors how Domain 3 (JS) auto-discovers `web/**/*.test.mjs`: the harness
finds test files by convention, runs them all, and aggregates results — no
central registry to update.
