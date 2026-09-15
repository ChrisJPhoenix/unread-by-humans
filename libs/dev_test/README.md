# dev_test

Shared library-tester orchestration used by all app runners in this project.

## Why

`run_test_cli` supports a Domain-1-only SOLO mode (`web_dir=None`) — Python
plan harness only, no domain headers, no aggregate summary — but no app shim
in this repository uses it.
`bin/dev test` calls it in AGGREGATE mode (`web_dir` set) and runs all three
domains: Domain 1 (Python plans), Domain 2 (Flask web smoke test), and Domain 3
(headless JS via `node:test`).  Centralising this logic here killed the
duplication that previously existed between the per-app runners and the
tkinter desktop app's own test entry point (not published in this repository).

`bin/dev test [--quick|--full|--learn] [libs...]` is the app-agnostic entry
point that runs all three domains over `libs/` + `web/` without requiring an
app context.  Its underlying module entry is `python -m libs.dev_test`.

## Public API

| Symbol | Description |
|---|---|
| `repo_root() -> Path` | Single source of repo-path truth; resolves from dev_test's stable `libs/` location so moving app directories does not break path resolution. |
| `run(argv, *, prog, web=True, web_dir=None) -> int` | Resolve repo paths and run all requested domains; `web=True` → all 3 domains over the shared `web/` tree, `web=False` → Domain 1 only, explicit `web_dir` overrides the default. Returns exit code 0/1/3/4/5/6. |
| `app_main(argv, *, prog, web=True) -> int` | Thin CLI router shared by `bin/dev`, `music`, and `create`; routes `test` → `run`, prints usage on `-h`/`--help`, returns 3 on unknown command. |
| `run_test_cli(argv, *, prog, libs_dir, web_dir, repo_root) -> int` | Parse argv and dispatch test domain(s); returns exit code 0/1/3/4. Pass `web_dir=None` for SOLO mode (Domain 1 only). |
| `summary_line(n_pass, n_fail, n_err, n_stub) -> str` | Format the human-readable summary line printed after all plans run. |
| `collect_unique(existing, new_items) -> list` | Return a new list = existing + new_items deduped, order-preserving, non-mutating. |
| `collect_unique_json(existing_json, new_json) -> str` | JSON-string wrapper for `collect_unique`. |
| `tally(statuses) -> list` | Return `[n_pass, n_fail, n_err]` from a list of status strings. |
| `tally_json(statuses_json) -> str` | JSON-string wrapper for `tally`. |
| `aggregate_exit_code(domain1_ok, domain2_ok, domain3_ok) -> int` | 0 if all three True, else 1. |
| `status_label(ok) -> str` | `"PASS"` or `"FAIL"`. |
| `failure_log_path(prog, tmp_dir_str) -> str` | Return the path string `<tmp_dir>/<prog>_test_last.txt` where failure logs are written. |
| `all_green_line(prog) -> str` | Return the brief success line printed when all tests pass. |
| `failure_notice_line(log_path_str) -> str` | Return the failure notice line that includes the log file path. |
| `progress_bar(done, total, label, width=20) -> str` | Pure bar formatter: returns a fixed-width `'#'/'-'` bar string with `\| done/total label` suffix; no I/O, safe to call from tests. |
| `silence_logging_probe() -> str` | Probe for the internal `_silence_logging` context manager. Installs a known baseline root logger (no handlers, `WARNING`) — it cannot use the ambient one, since it runs nested inside `run_test_cli`'s own outer wrap — then emits a warning before, inside, and after the context, returning `"before=<n> inside=<n> after=<n> restored=<bool>"`. The post-context emission is what makes restoration observable rather than merely asserted. |

A `WORK_PROGRESS`-gated single-line live progress bar is rendered to `/dev/tty` during a run (bypassing `run_test_cli`'s stdout/stderr buffering and any caller `>/dev/null 2>&1`), no-op when `WORK_PROGRESS` is unset or no tty exists.  This is handled internally by `_ProgressReporter`; callers need not do anything.

Internal helpers (`_dispatch_domains`, `_run_domain1_plans`, `_run_domain1_learn`,
`_run_domain2_smoke`, `_launch_domain3_js`, `_collect_domain3_js`, `_print_aggregate_summary`,
`_print_step_failures`, `_print_stub_list`, `_ProgressReporter`, `_silence_logging`) are not exported.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All plans (and domains) passed. |
| 1 | At least one plan or domain failed. |
| 3 | Usage error (`--learn` with no lib named, or no plans found). |
| 4 | Stub-coverage failure (orphan `@stub` or orphan plan file). |
| 5 | The run exceeded its hang-detection timeout (see below). |
| 6 | The hang-detection ceiling file is missing or malformed (see below). |

See `design/testing.md` for plan-file grammar, stub contract, and harness modes.

## Output behavior

On success (exit 0), the runner suppresses all per-plan detail and prints only a brief `all green` line.
On failure (exit 1/3/4), the full output is written to `<repo_root>/tmp/<prog>_test_last.txt` and that path is printed as the last stdout line.
`--learn` mode is exempt and prints diffs interactively as before.

`run_test_cli` also silences the Python `logging` module for the duration of a run (root `NullHandler` + level above `CRITICAL`, restored afterward), so a library's `logger.warning(...)` diagnostics never pollute the failure log. See `design/testing.md` § "Log records are not test output" for the rationale.

## Hang-detection timeout

`libs/dev_test/timeout_seconds.txt` holds two whole-second lines, one per
mode (`quick 900` / `full 900`) — the SIGALRM ceiling a test run is armed
with. `--full` runs every `test_*.txt` and is materially slower than
`--quick`, so the two modes are held to separate ceilings;
`ceiling_mode_from_argv(argv)` picks which one governs a run (`--learn`
counts as `full`). The file is committed on purpose: it is a shared hang
ceiling, not machine-local timing, so every clone starts from the same
values. A missing or malformed file (wrong field count, unknown/repeated
mode, non-integer or non-positive value, or not exactly the two required
modes) raises `TimeoutFileUnusable` — a hard failure, not a silent default,
since the ceiling is the only thing standing between an infinite hang and a
wedged session. When a run takes more than two thirds of its mode's stored
ceiling, the runner rewrites that mode's line to `2x` the observed duration
(the other mode's line is untouched); a faster run leaves both alone. Each
mode's value only ever ratchets up — its job is catching an infinite hang,
not enforcing a performance budget, so a suite that grows over time is
accommodated automatically. Every run prints a `dev test: <mode> run took
Ns (<mode> ceiling Ms)` line regardless of outcome. See
`parse_timeout_file_json`, `render_timeout_file`, `ceiling_mode_from_argv`,
`next_timeout_seconds`, `run_duration_line`, and `TimeoutFileUnusable` in
`dev_test.py`.

At runtime, `run()` — not `run_test_cli`, since `run()` is the single entry
point every app runner (in this repository, `bin/dev`) funnels through — reads
the governing mode's stored ceiling (returning exit code
`TIMEOUT_FILE_UNUSABLE_EXIT_CODE`, `6`, on `TimeoutFileUnusable`) and arms a
SIGALRM watchdog at it before dispatching. A trip raises `_WatchdogTimedOut`
(a `BaseException`, so `run_test_cli`'s `except Exception:` cannot absorb
it), which `run()` catches and reports as exit code `TEST_TIMED_OUT_EXIT_CODE`
(`5`), writing the timeout message to `tmp/<prog>_test_last.txt` so
`bin/green`'s inline print isn't stale. The message also names which phase
the run was in — the local test domains, or the specific gated live test
that was executing (`running_live_test`, kept current by `_run_live_tier`)
— because an overrun inside a real Claude session over the network is an
expected occasional cost, while a trip with no live test running is a
genuine wedge worth hunting. A run that completes normally and
took more than two thirds of its ceiling rewrites that mode's stored value
with `2x` the observed duration — but a run that *trips* the watchdog never
rewrites it, since a genuine hang doubling its own ceiling on every retry
would defeat the whole feature.
