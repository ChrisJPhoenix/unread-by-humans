# Testing

**Date:** 2026-05-19
**Status:** Phase 0 — lands with the harness

---

## Purpose

The test harness is the spine that holds the rest of the
project upright. Every library in `libs/` ships a primary test
plan in the same commit; nothing is added without one. This
document is the spec for *what the plans are* and *what the
harness does with them*.

The two design principles this doc implements (from the source monorepo's
top-level plan document, not published here):

- **Test harness lands first and never lags.**
- **Stub before solve.**

---

## Layout

```
libs/
  <libname>/
    __init__.py
    <impl>.py
    README.md
    test_primary.txt        # the library's main plan (quick mode)
    test_primary_<feature>.txt  # optional: also quick mode, for a split library
    test_<algorithm>.txt    # optional: extra plans run in full mode
    test_stub_<fn>.txt      # required iff <fn> is @stub-decorated
```

Plans live next to the library they test. The harness does not
scan a separate `tests/` directory.

---

## Plan-file format

A plan file is human-readable text. It is meant to be inspected
and edited by hand — 2-D arrays must read as 2-D arrays, not as
serialized lists.

### Grammar

```
file        := stanza ( blank+ stanza )*
stanza      := comment* call-line field*
call-line   := 'call:' WS dotted-name
field           := arg-field | save-field | expect-field | normalize-field | tolerance-field | comment
arg-field       := 'arg' WS name WS '=' WS value-or-block
save-field      := 'save' WS 'as:' WS name
expect-field    := 'expect' WS name WS '=' WS value-or-block
normalize-field := 'normalize:' WS pattern WS '=>' WS replacement
tolerance-field := 'tolerance:' WS float
comment         := '#' .* EOL
```

- A stanza is one function call plus its args and expectations.
- Stanzas are separated by one or more blank lines.
- Lines whose first non-blank character is `#` are comments.
- The `call:` line must be the first non-comment line of its
  stanza.
- A `call` of the form `<lib>.<fn>` resolves to
  `libs.<lib>.<fn>` at run time.

### Values

| Form             | Parsed as              |
|------------------|------------------------|
| `42`, `-7`       | `int`                  |
| `3.14`, `-0.5`   | `float`                |
| `true` / `false` | `bool`                 |
| `null` / `none`  | `None`                 |
| `"hello\n"`      | `str` (escapes `\n \t \" \\` and `\xHH`) |
| `$name`          | reference to a saved variable      |
| indented block   | 2-D numeric NumPy array (rows of space-separated numbers) |

A block follows a `=` with nothing after it; subsequent
indented lines belong to the block until the next blank line
or non-indented line.

`\xHH` (exactly two hex digits) is the general form for a character the
grammar cannot otherwise carry, and the only way to write a control byte: a
literal one does not survive the plan file, since plan files are read with
`Path.read_text()`, whose universal-newline translation rewrites a CR before
the grammar sees it. `\x0D` in a `"..."` value reaches the called function as
a real CR. `\x` not followed by two hex digits is a parse error rather than a
silent fallback, and there is deliberately **no** `\r` escape — an unknown
escape keeps its letter, so the two-character `\r` sequences inside embedded
JSON keep meaning backslash-then-`r`. Learn mode emits the same spelling:
a returned control character is written back as `\xHH`.

### Expect fields

| Field      | Asserts                                                        |
|------------|----------------------------------------------------------------|
| `return`   | the call returned this value (equality or float-tolerance)     |
| `raises`   | the call raised an exception with this class name              |
| `prints`   | stdout captured during the call equals this                    |
| `contains` | the return value (a string) contains this substring            |

If a stanza has no `expect` field, the harness only checks that
the call did not raise.

`expect contains = "substring"` is a substring check against the function's
return value.  Use it when the exact return string is non-deterministic in
order (e.g., a list of error messages where order may vary) but a key substring
is always present.

### Normalization

A stanza may carry one or more `normalize:` lines, each of the form:

```
normalize: pattern => replacement
```

Multiple rules are allowed; they are applied in declared order via Python
`re.sub`. The `pattern` and `replacement` text is **raw** — regex
backslashes such as `\d` are not string-escaped (unlike quoted `"..."` arg
values).

Before comparing a string `expect return` or `expect prints` value, the
harness rewrites **both** the actual output and the expected value through
all normalization rules, then compares the two masked strings. The author
therefore writes the masked form in the `expect` line.

Example: with the rule `id-\d+ => id-<n>`, actual output `id-7` and
expected value `id-<n>` both normalize to `id-<n>`, so they match.

The `<placeholder>` convention (`<n>`, `<date>`, `<normalized>`, …) is an
author-chosen marker that signals to a human reader that the value is
masked, not literal. A count should appear as `<normalized>`, not a
misleading `0`.

`--learn` writes the masked form into rewritten expects, so learn and
normalize compose correctly.

Normalization applies **only** to string `return`/`prints` comparisons. It
does not affect numeric-block expects or non-string values.

```
call: report.render
normalize: generated:\s+\d{4}-\d\d-\d\d => generated: <date>
expect prints = "report generated: <date>"
```

### Float tolerance

A target value is **float-spelled** when its literal cannot be parsed as a plain
integer — it contains a decimal point or exponent (e.g. `3.0`, `1e3`). Otherwise
it is **integer-spelled**.

- A **float-spelled** target compares with relative tolerance: it matches iff
  `abs(actual - expected) <= tolerance * abs(expected)`. A float-spelled target of
  exactly zero would have a relative band of zero, so instead its band is set by how
  many decimal places it is written with: `tolerance * 10**(1 - d)` where `d` is the
  count of fractional digits. Concretely with the default `1e-6`: `0.0` -> band
  `1e-6`, `0.00` -> `1e-7`, `0.000` -> `1e-8` — each extra written zero tightens the
  band tenfold. Every nonzero target keeps the pure relative band
  `tolerance * abs(expected)` (so e.g. `0.0005` is held to `tolerance * 0.0005`).
- An **integer-spelled** target requires exact equality.
- The default tolerance is `1e-6` (one part per million of the target value). A
  per-stanza `tolerance: <float>` line overrides it for every float-spelled
  comparison in that stanza; `tolerance: 0` forces exact equality even on
  float-spelled targets.
- In a **numeric block**, spelling is judged per element. In a row `2 3.0`, the
  `2` must match exactly while the `3.0` gets the tolerance band.
- **Booleans, `None`, and strings** always compare exactly; normalization rules
  are never affected by tolerance.
- A `$var`-sourced numeric value carries no literal spelling; it falls back to
  dtype — a float array/scalar is tolerant, an integer one is exact.

```
call: physics.trajectory
tolerance: 1e-3
expect return =
  0.0  9.81
  3    20.0
```

Here `3` must match exactly; `9.81` and `20.0` are compared within the 1e-3 relative band; `0.0` uses the digit-scaled band `1e-3 * 10**(1-1) = 1e-3` (one decimal place).

### Variables

`save as: <name>` after a stanza stores the call's return value
in a local variable. `$<name>` in a subsequent `arg` or
`expect` value re-uses it. Variables live for the duration of a
single plan run.

### Example

```
# Pin and sample once.
call: seed_registry.pin
arg master_seed = 42

call: seed_registry.sample_uint64
arg name = "alpha"
save as: alpha1

# Re-pin: cache cleared, same name should reproduce.
call: seed_registry.pin
arg master_seed = 42

call: seed_registry.sample_uint64
arg name = "alpha"
expect return = $alpha1
```

---

## Harness modes

`bin/dev test [--quick|--full|--learn] [<lib>...]`

| Mode       | What it runs                                          |
|------------|-------------------------------------------------------|
| `--quick`  | every `test_primary*.txt` in each (selected) library. Default. |
| `--full`   | every `test_*.txt` in each (selected) library.        |
| `--learn`  | rewrites `expect` blocks with the actual values and prints a unified diff. Only when libraries are explicitly named — never on the whole tree. |

Library filters are positional after the flag:
`bin/dev test --full binarize extract`.

---

## Stub contract

A function annotated with `@stub` (from
`libs.test_harness.stub`) is a placeholder. The harness applies
the following rules — these are the rules that make "stub
before solve" auditable:

1. **Every `@stub` function MUST have a `test_stub_<fn>.txt`
   alongside its library.** The literal prefix `test_stub_`
   distinguishes stub-contract plans from regular plans; the
   suffix is the function's own name. The stub function must
   pass that plan as it stands. This is the contract the stub
   commits to even before the real implementation lands.

2. **Every `test_stub_<fn>.txt` MUST name a real `@stub`
   function.** Orphan plan files are a coverage failure.

3. **A non-stub function must pass every `test_*.txt` that
   calls it** (directly or transitively).

4. **A `test_<plan>.txt` that reaches a `@stub`-decorated call
   must pass up to the point the stub is invoked.** Execution
   halts at the stub boundary; later steps in that plan are
   not run. The steps up to and including the stub-emission
   count as "passed with stub deferral", not as failures.

5. **For each stubbed function encountered, the harness prints
   exactly one line: `Stubbed: <library>:<function>`.** This
   makes the deferred coverage explicit in test output, so
   real-vs-stubbed coverage is auditable from logs.

Rules 1 and 2 are checked before any plan runs;
`bin/dev test` exits 4 if either fails. Rules 3 and 5 are
enforced by the runner. Rule 4 is the run-time semantics.

The stub's own `test_stub_<fn>.txt` is *not* halted by rule 4 —
that plan is specifically there to exercise the stub.
Detection is filename-based: a plan whose path matches
`libs/<lib>/test_stub_<fn>.txt` runs `<lib>.<fn>` normally; any
other plan triggers the halt.

---

## Self-test

`libs/test_harness/test_primary.txt` exercises the harness
through itself, by feeding inline plan strings to `run_text`
and asserting on the resulting `PlanResult`. It covers:

- stanza parsing (count and blank-line separation)
- the three result statuses (`pass`, `fail`, `error`)
- `expect raises`
- parse errors reported as result status `error`, not crashes
- stub-boundary halting and the `Stubbed:` line
- plan discovery
- the stub-coverage check
- normalization masking (`normalize:`) on pass, fail, and learn round-trip
- float-spelled tolerance: per-element block spelling (int vs float), default 1e-6 band, `tolerance: 0` override, and digit-scaled zero-target band (`0.0` -> 1e-6, `0.00` -> 1e-7, `0.000` -> 1e-8)

If `app-name test --quick test_harness` is green, the harness
itself is healthy.

---

## Exit codes

`app-name test`
specifically uses:

- `0` — all plans passed.
- `1` — at least one plan failed or errored.
- `3` — usage error (e.g. `--learn` with no library named, or a library filter that matches no plans). The latter holds in aggregate mode (`bin/dev`, `bin/green`) as well as in the shared runner's Domain-1-only SOLO mode — an unfiltered run finding no plans keeps its own code instead: exit 1 in aggregate mode, exit 3 in SOLO mode. No app shim in this repository uses SOLO mode.
- `4` — stub-coverage failure (orphan `@stub` or orphan plan).
- `5` — the run exceeded its hang-detection timeout.
- `6` — the hang-detection ceiling file is missing or malformed.

On exit 0, the runner prints only a brief success line (`<prog> test: all green`) and no per-plan details.
On exit 1, 3, or 4, the full output (stdout and stderr from all domains) is written to `<repo_root>/tmp/<prog>_test_last.txt`; that path is printed as the last stdout line. This lets callers verify success by exit code alone and locate failure details without specifying a custom log path.
`--learn` mode is exempt: it always prints diffs interactively.

`bin/green` wraps this contract into one allow-listed command for the inner loop: it prints the diff since the last green checkpoint (`git diff HEAD` plus untracked files), runs `./bin/dev test` (quick by default; `--full` and library filters are passed through), prints `RC=<n>`, and on a non-zero RC prints `tmp/dev_test_last.txt` inline. On exit 0 it records a local `green checkpoint` commit on the current branch (only when the tree is dirty) so the next run's `git diff HEAD` shows just the changes since the last green.

**Gate serialisation (`tmp/gate.lock`).** `bin/green` and `bin/dev test` each take a per-worktree lock before running, via `bin/gate_lock.sh` — an atomic `mkdir` of `<repo_root>/tmp/gate.lock`, released by an `EXIT` trap, refusing rather than blocking. Two gate runs in one worktree otherwise race on the git index (`bin/green` commits a checkpoint), on `tmp/<prog>_test_last.txt` (written on red but *unlinked* on green, so one run deletes the other's failure log — the observed phantom-red), on the `libs/dev_test/timeout_seconds.txt` ceiling ratchet (a tracked file written mid-run, which also dirties the tree under the other run's `git add -A`), on `tmp/live_gate_cache.json` (non-atomic read-modify-write), and on the fixed-name `tmp/live_gate_cache_roundtrip_probe.json`, whose write-then-read the other run can overwrite into a phantom red from the harness's own probe. Contention exits **7**, distinct from every `dev test` code (1, 3, 4, 5, 6). The lock records its owner pid and is reclaimed if that pid is gone, because a `SIGKILL`ed run would otherwise brick every later gate. Scope is deliberately per-worktree: every contended path resolves from the worktree's own root. The one genuinely cross-worktree write, `MAIN/tmp/serve/stale.json`, is a best-effort whole-file recompute that self-heals on the next green, and is not covered. This is shell/file glue and is untested-by-design; every subsequent gate run exercises the acquire path.

---

## Hang-detection timeout

The ceiling lives in `libs/dev_test/timeout_seconds.txt` as two whole-second
lines, one per mode:

```
quick 900
full 900
```

`--quick` and `--full` are held to **separate** stored ceilings — `--full`
runs every `test_*.txt` in every library and is materially slower than
`--quick`'s `test_primary*.txt`-only sweep, so one ceiling cannot honestly
serve both. `ceiling_mode_from_argv(argv)` decides which ceiling governs a
given run; `--learn` is treated as `full` because a ceiling that is too
generous merely delays catching a hang, whereas one that is too tight kills a
healthy run outright, so the roomier of the two is the safe default for a
mode `run()` doesn't otherwise size. The file is committed on purpose: it is a
shared property of the test suite, not machine-local timing, so every clone
starts from the same values.

A missing file, an unreadable file, or one that fails to parse (wrong number
of fields on a line, an unknown or repeated mode, a non-integer or non-positive
value, or a file that doesn't define exactly `quick` and `full`) raises
`TimeoutFileUnusable` and is a **hard failure** — exit code `6` — rather than
falling back to a silent default. The ceiling is the only thing standing
between an infinite hang and a wedged session, so a silent default would hide
the fact that the ceiling in force is no longer the value anyone actually
chose; a run that cannot read a trustworthy ceiling must fail loudly instead
of guessing one.

`run()` arms a SIGALRM watchdog at the governing mode's stored ceiling before
dispatching `run_test_cli`. A trip returns exit code `5` and writes the
watchdog's timeout message (naming the elapsed time, which mode's ceiling
tripped, and whether the run was in the local test domains or inside a
named gated live test — the live case worded as an expected occasional
overrun of a real network call rather than a hang to hunt) into
`tmp/<prog>_test_last.txt` — a timed-out run has no
buffered output of its own to write, but `bin/green` prints that file inline
on any non-zero exit code, so it must not be left holding a *previous* run's
contents.

Every run — win or lose, ratcheted or not — prints a `dev test: <mode> run
took Ns (<mode> ceiling Ms)` line, so the timing is visible without waiting
for a ratchet to happen.

A completed run that took more than two thirds of its mode's ceiling
rewrites the file with `2*S` for that mode only (`S` being the observed run
duration); the other mode's stored ceiling is left untouched. This is stable:
the next run of the same length then sits at exactly half the new ceiling and
does not ratchet again.

Each mode's ceiling **only ever rises**. Its job is catching an infinite
hang, not enforcing a performance budget, so the seed values are deliberately
loose and a suite that gets faster does not tighten them — and, critically, a
run that *trips* the watchdog never rewrites the file, or a genuine hang
would double its own ceiling on every run and defeat the whole feature.

The watchdog is skipped (not armed) when called off the main thread, where
`signal.signal` is unusable. `_WatchdogTimedOut` derives from `BaseException`,
not `Exception`, so `run_test_cli`'s `except Exception:` around
`_dispatch_domains` cannot absorb it.

---

## The shared runner (`libs/dev_test`)

The three-domain orchestration lives in the shared library `libs/dev_test`.
App runners delegate their `test` subcommand to:

```
run_test_cli(argv, *, prog, libs_dir, web_dir, repo_root) -> int
```

- **SOLO mode** (`web_dir=None`): the shared runner supports a
  Domain-1-only mode, with no domain headers and no aggregate summary; the
  app shim that used it does not ship here.
- **AGGREGATE mode** (`web_dir` set): all three domains + the
  `=== <app> test summary ===` block.  This is what `bin/dev test` uses.
  A filter matching no plans is exit 3, checked before any
  domain runs; an unfiltered empty run is still a Domain-1 failure → exit 1.

Exit codes 0/1/3/4 are unchanged across both modes (see `## Exit codes` above).

On exit 0, `run_test_cli` prints only a brief success line (`<prog> test: all green`) and suppresses all per-plan detail. On exit 1, 3, or 4, the full captured output (stdout and stderr) is written to `<repo_root>/tmp/<prog>_test_last.txt`; that path is printed as the last stdout line. `--learn` mode is exempt and always prints interactively.

A `WORK_PROGRESS` env-gated live progress bar is written directly to `/dev/tty` (bypassing `run_test_cli`'s stdout/stderr buffering and any caller `>/dev/null 2>&1`), disabled when the env var is unset or no tty exists. This lets a caller see progress even though test output is otherwise suppressed.

**Log records are not test output.** For the duration of a test run,
`run_test_cli` wraps the work in a silencing context over the root
`logging` logger — a `NullHandler` (so `logging.lastResort` never fires) plus a
level above `CRITICAL` (so records are dropped at the source, since the
library loggers set no level of their own and inherit root's). Both the prior
handler list and level are restored on the way out, however the run exits.

**The silencing deliberately outlives the output redirect.** It spans *both*
`_dispatch_domains` and the gated-live tier (`_run_live_tier`, below), even
though only the former runs inside the stdout/stderr redirect. The live tier is
intentionally unbuffered so its audit lines show up on green — which means a
library `logger.warning(...)` fired during a live test would otherwise go
straight to the terminal, reintroducing exactly the clutter this removes, on
the one tier where it is most visible. Silencing `logging` costs the live tier
nothing: its audit lines are `print()` calls and are unaffected. Hence
`_silence_logging()` is entered as its own `with`, outside the one holding the
redirect, rather than sharing it.

The rule this encodes: **in a test, a condition either fails the test or is
silent.** A `logger.warning(...)` is neither an assertion nor something a human
reads — in the failure log dumped inline by `bin/green` it is pure clutter that
buries the actual failure. Concretely this is what keeps an incidental
library warning (e.g. a clipping-detected notice from a library's own write
path) out of `tmp/<prog>_test_last.txt`.

The loggers themselves are deliberately left in place: they still emit
normally when the server or an app runs outside the harness. A diagnostic that
genuinely matters to a *human* does not belong in a log line at all — it must
become either a plan expectation (so it fails the test) or a surface in the
web UI.

### `bin/dev` — the app-agnostic test command

`bin/dev test [--quick|--full|--learn] [libs...]` is the canonical,
app-independent entry point for testing all libraries.  It runs all three
domains over `libs/` + `web/` and is the right tool when you are not working
inside a specific app context.  The underlying module entry is
`python -m libs.dev_test`.

**Adding a library is enough to be covered:**

- A Python library under `libs/<name>/` needs only a `test_primary.txt`
  alongside it.  Domain 1 auto-discovers it; `--full` additionally picks up any
  extra `test_*.txt` files in the same directory.
- A library split into per-feature modules splits its plan the same way, and
  names each piece `test_primary_<feature>.txt` so that **all of them stay in
  the quick tier**. This matters because `bin/green` gates on `--quick`: a plan
  named anything else drops out of the gate and is only reached by `--full`.
  Naming a plan `test_<scenario>.txt` is therefore a deliberate choice to run
  it in full mode only, not the default for a split.
- A JS module is covered by placing its unit tests at
  `web/static/<name>.test.mjs`.  Domain 3 discovers it automatically.
- A web project is covered by dropping a `web/smoke/<project>.py` that exposes
  `run(ctx, check)` (and an optional `seed(ctx)` for fixtures only that project
  needs).  Domain 2 discovers it automatically — mirroring how a JS module is
  covered by adding `web/static/<name>.test.mjs`.

**App shims and path stability.** The apps are thin shims delegating to
`libs/dev_test`: `music` and `create` via `app_main(..., web=False)`
(Domain 1 only); `bin/dev` runs all 3 domains via `python -m libs.dev_test`.
Path resolution lives in
`dev_test.repo_root()`, which resolves from `dev_test`'s own stable location
inside `libs/` — moving app directories will not cause path breakage.

Exit codes 0/1/3/4 are identical to those described in `## Exit codes` above.

---

## The three domains

`bin/dev test` aggregates **three independent domains** over the shared `libs/`
and `web/` trees. (The SOLO-mode runners run Domain 1 only — see above.)

### Domain 1 — Python plan harness over `libs/`

Reuses the same `libs.test_harness` public functions as the SOLO-mode
runners. Modes, plan-file format, stub contract, exit semantics, and
stub-coverage checks are **identical** to those described above. Domain 1
honours `--quick`, `--full`, and `--learn` with the same semantics. The
plan-loop itself is the shared `libs/dev_test` implementation.

### Domain 2 — Flask web smoke test

Domain 2 auto-discovers per-project smoke files via the glob `web/smoke/*.py`.
Files whose basename starts with `_` (e.g. `web/smoke/_harness.py`) are skipped
by the glob and are infrastructure, not project files.

`web/smoke/_harness.py` is the shared entry point.  It builds one shared temp
root, creates the Flask test clients (a context object with `client`,
`mini_client`, and `real_client`) by importing `create_app` from `web/server.py`
(no real network socket is opened), then for each discovered project file calls
its optional `seed(ctx)` (fixtures that only that project needs) followed by its
required `run(ctx, check)`.  Pass/fail counts are aggregated dynamically — there
is no hardcoded total.

The shared runner `dev_test._run_domain2_smoke` loads `web/smoke/_harness.py`
and calls its `main()`.

### Domain 3 — Headless JS via `node:test`

Runs all `web/**/*.test.mjs` files with Node's **built-in** `node:test` runner
(Node >= 18 required). Tests are native `.mjs` ESM files and use only
`node:test` and `node:assert` — **zero npm dependencies** (no `package.json`,
no `node_modules`).

A missing `node` binary or no `*.test.mjs` files found is a **hard fail**:
domain 3 returns nonzero and prints an install message; `bin/dev test` as a
whole returns nonzero.

### Modes

| Flag | Effect |
|------|--------|
| `--quick` (default) | All three domains at quick depth. |
| `--full` | Deepens **domain 1** only (every `test_*.txt`); domains 2–3 are unchanged. |
| `--learn <lib>` | Applies to **domain 1 only**; refuses without a named lib (exit 3). |

### Exit codes

Mirror `bin/dev test`:

- `0` — all three domains passed.
- `1` — at least one domain failed or errored.
- `3` — usage error (e.g. `--learn` with no library named, or a library filter that matches no plans). The latter holds in aggregate mode (`bin/dev`, `bin/green`) as well as in the shared runner's Domain-1-only SOLO mode — an unfiltered run finding no plans keeps its own code instead: exit 1 in aggregate mode, exit 3 in SOLO mode. No app shim in this repository uses SOLO mode.
- `4` — stub-coverage failure in domain 1.
- `5` — the run exceeded its hang-detection timeout.
- `6` — the hang-detection ceiling file is missing or malformed.

---

## Gated live tests (throttled, narrowly content-fingerprinted)

Some behavior can only be verified against a **real external service** — the
Claude SDK/API over a real socket. Such a check costs
money, is non-deterministic, and needs credentials, so it can be neither a
`call:`-plan nor an inner-loop gate. A **gated live test** runs it
**automatically but throttled**, so it can never bit-rot unnoticed while costing
only pennies/day (decisions with Chris, 2026-07-16).

**What a gated live test declares.** A gated live test is a module discovered by
the naming convention **`libs/*/live_test_*.py`** exposing two callables: a
`covered_paths()` returning the **narrow** list of files/libs whose change should
retrigger it (scoped tightly: an edit outside the set never triggers it — "run
only when the *relevant* code changes"), and a `run()` that performs the live
check (raises / returns non-zero on failure). Discovery imports the module and
calls `covered_paths()` always (so a broken import is caught even when the tier
is skipped); `run()` is invoked only when the gate says RUN. Keep heavy/live
imports (the SDK, the socket) *inside* `run()` so discovery stays cheap and safe.

**No live test ships here.** No `libs/*/live_test_*.py` module ships in this
repository: the live membrane it would guard is a real call to the Claude
API, and that call belongs to another app, not to this one. Discovery
therefore finds nothing, and the tier passes silently on every run. What is
published is the mechanism — `libs/live_gate`'s pure `gate_decision` and
`dev_test._run_live_tier`'s wiring — plus its own unit tests, not a live
check of anything.

**The green-cache.** Keyed by test id, stores `{last_green_epoch,
covered_fingerprint}` under `movie/tmp/` — **regenerable** (a cleared cache just
means "run it", which is fail-safe) and **never committed** (it is machine- and
time-local; committing it would cause nondeterministic churn and false skips on
another machine).

**The gate decision (a PURE, unit-tested function).** Given the cache entry,
`now`, `TTL = 4h`, the current fingerprint, and whether creds are present, it
returns RUN / SKIP-as-pass / FAIL-no-creds:

- **FAIL-no-creds** iff credentials are absent — surfaced as **red** (decision
  with Chris, 2026-07-16). Chris expects creds to always be present on the machine
  that runs the tier; their absence means something is broken that he needs to
  fix, so it must not pass silently. Keyless/automated environments never reach
  this state because they suppress the whole tier via `--no-live-tests` (below),
  *not* via a creds-skip. **Amended 2026-08-15:** a caller may opt into
  skip-as-pass for exactly this case by passing `skip_if_keyless` (the
  `--skip-live-tests-if-keyless` flag, below), which `bin/green` now always does.
  The default is still FAIL, and an explicit `force` still beats the opt-in.
- **RUN-forced** iff `force` is set (the `--force-live-tests` flag, below) — but
  *after* the creds check, so forcing can never manufacture a run that has no
  credentials to make. Reason string `forced`.
- **RUN** iff any of: no cache entry · `now − last_green ≥ TTL` (the 4h fallback —
  a rare situation) · `current_fingerprint ≠ stored` (a *relevant* code change) ·
  the cache is unreadable or the clock skewed (`now < last_green`).
- **SKIP-as-pass** otherwise (fresh and unchanged).

**Fail-safe direction.** Every ambiguous case (missing/corrupt cache, no
fingerprint, clock skew) resolves to **RUN**. A false run costs pennies; a false
*skip* is the only dangerous outcome and is thereby made structurally impossible.
Absent creds is the one non-run non-skip outcome: it fails loudly rather than
skipping, so broken credentials can never masquerade as coverage.

**The narrow fingerprint.** A hash over ONLY the declared `covered-paths` set's
current contents (sorted, stable). This is the whole of "scope it narrowly": an
edit to an unrelated library does not bust the cache; an edit to a covered file
does, and retriggers immediately (catching a *local* regression the 4h timer
alone would mask for up to 4h). Active editing of covered code therefore
retriggers on each green-gate — acceptable and self-limiting, since that is
exactly when live verification is most wanted, and each run is cents.

**On a real run:** green → restamp `{now, fingerprint}`; failure → do not stamp,
surface as red (this is how bit-rot gets noticed).

**Where it runs (policy).** The tier is woven into `bin/dev test` by default and
suppressed by an explicit **`--no-live-tests`** CLI flag (decision with Chris,
2026-07-16 — a passed-through argument, not an environment variable, so it rides
`bin/green`'s `"$@"` pass-through and the existing argparse cleanly).

**Overriding the throttle: `--force-live-tests`** (decision with Chris,
2026-07-31). The throttle above is a *cost* policy, and there are times when the
answer to "is the live membrane actually working right now?" is wanted on demand
rather than on the cache's schedule — debugging a live failure, or confirming a
fix without editing a covered file just to bust the fingerprint. `--force-live-tests`
makes the gate return RUN for **every** discovered live test on **every**
invocation, whatever the cache says. Three properties keep it from becoming a
loophole:

- It is **mutually exclusive** with `--no-live-tests` (argparse-enforced): the two
  express opposite intents, and silently letting one win would make the more
  dangerous outcome — a spend the caller didn't intend — the accidental default.
- It **does not bypass FAIL-no-creds**. Forcing overrides *throttling*, not
  *feasibility*; the creds check runs first, so a keyless environment still goes
  red instead of attempting an unauthenticated call.
- A forced run that passes **still restamps** the green-cache. A real run is real
  coverage no matter what prompted it, and restamping is what keeps a forced run
  from being charged twice.

Its audit line is **`Gated (forced): running <id>`** — distinct from the
throttled `Gated (stale/changed):`, so a reader of the output can always tell a
run the gate *chose* from one the caller *demanded*. The flag rides `bin/green`'s
`"$@"` pass-through like `--no-live-tests`, so `bin/green --force-live-tests` is
the full-cost dev-loop invocation. It is deliberately **not** a default and not an
environment variable: every forced run should be a visible, per-invocation choice
to spend money.

**Tolerating a keyless machine: `--skip-live-tests-if-keyless`** (decision with
Chris, 2026-08-15). `bin/green` is the verify command every agent and worker runs,
and on a machine with no key the FAIL-no-creds rule made it red for a reason that
has nothing to do with the diff under test — so the signal a worker green-gates on
was reporting the environment, not the change. The flag makes absent credentials a
**skip-as-pass** for that caller only. Chris named this the one deliberate
exception to "never weaken a test", and it is narrow by construction:

- The decision stays in the **pure, unit-tested** `gate_decision` (a
  `skip_if_keyless` parameter), not in shell. There is exactly one credential
  predicate — `dev_test._live_creds_present` — and it is not duplicated in bash.
- It is **not** in the `--no-live-tests` / `--force-live-tests` mutually-exclusive
  group, so it composes; but an explicit **`--force-live-tests` overrides it** and
  still goes red without creds. Forcing is an on-demand demand for a real call.
- It changes nothing when creds *are* present: the throttle, the fingerprint, and
  the red-on-failure rule all behave exactly as before.
- `bin/dev test`'s default is unchanged. Only `bin/green` passes the flag.

Its audit line is `Gated (skip, no creds (keyless caller)): <id>` — a keyless skip
is distinguishable from a fresh-cache skip in the output, so "this machine never
had a key" can never be mistaken for "this was verified recently".

- **`bin/dev test` / `bin/green`** (Chris's dev loop): the gated-live tier is
  woven in, self-throttled. When it runs and fails it goes **red** — that is the
  point. When fresh it is a silent-but-audible skip (print
  `Gated (fresh, 47m ago): <id>` on skip, `Gated (stale/changed): running <id>`
  on run — auditable like the `Stubbed:` line, so "when did the SDK last really
  pass" is always visible). `bin/green` additionally always passes
  `--skip-live-tests-if-keyless` (above), so a keyless machine skips rather than
  reds.
- **Automated / worker green-gates:** run with **`--no-live-tests`**, so automated
  builds never spend API budget and never hit the FAIL-no-creds path in a keyless
  sandbox. That flag remains the way to suppress the tier *deliberately*;
  `--skip-live-tests-if-keyless` only covers the *involuntary* keyless case.

No scheduler: coverage rides Chris's normal test cadence — idle days (no runs,
no changes) need no coverage, by design.

The tier's `print()` audit lines are unbuffered by design (that is the point of
running it outside the redirect), but Python `logging` **is** silenced across it
— see § "Log records are not test output" above for why the two are separable.

## Tests and live state

**The standing rule: a test must not read live state.** A plan that asserts
against whatever happens to be on disk, in a running server, or in a
user-owned directory fails for reasons unrelated to the change under test, and
goes green again for reasons equally unrelated. Inject the value, mock the
source, or point the code at a scratch path built by the test. The gated
live tier above is not a counter-example: it is throttled, fingerprinted, and
exists precisely because the *external service* cannot be faked.

---

## What this doc does *not* yet cover

- Performance / profiling-integrated plans.
