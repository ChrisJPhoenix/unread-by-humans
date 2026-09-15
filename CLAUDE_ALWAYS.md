# Non-negotiable rules INCLUDING FOR AGENTS - These rules must ALWAYS be followed.

**Use venv.** Python venv is at `venv/` in the repo root.
Always use `venv/bin/python`
and `venv/bin/pip`. Never use system Python.

**No side projects.** Don't add features, refactor, or introduce
abstractions beyond what the task requires. But bug fixes are not a new
feature or abstraction. Don't spend effort looking for bugs, but if you
notice a bug, double-check your analysis and then fix it or ask the user if
you're still not sure; then report the bug and your fix.

**Don't use `cat` or `echo` to create files.** Use Write/Edit.

**Don't use `sed` to read from files.** Use Read.

**Don't begin commands with "cd".** You're probably already in the directory you want, and if not, it's outside the sandbox and you should ask the user if you want to go there. Putting "cd" in a command makes it harder to approve.

**Prefer `bin/look`; flag its gaps instead of working around them.** Use
`bin/look` (def/refs/outline/pattern/grep/echo/find/seq/each) instead of shell
`grep`/`echo`/`find`/for-loops — it is one allowlistable, side-effect-free call,
and shell `echo` is hook-blocked. If you want `grep` or `echo` (or a find/loop)
for something `bin/look` cannot yet do, do NOT silently fall back to a raw shell
pipeline: name the missing capability to the user (sub-agents: surface it in your
report) so they can add it to `bin/look` for next time.

**Temp files go in the repo's `tmp/`.** Use `<repo-root>/tmp/`. Never use
`/tmp/` or `$TMPDIR`.

**Stub before solve.** A new library starts as an
`@stub`-decorated function (from `libs.test_harness.stub`)
returning plausible dummy data, plus a `test_stub_<fn>.txt` plan
that the stub passes. The real algorithm replaces the stub later
under the same plan, extended as needed. See `design/testing.md`
§Stub contract.

**Test plan lands with the code.** Never commit a library without
its `test_primary.txt` in the same change. New public functions
extend an existing plan or get a new one.

**Use the test harness, not throwaway scripts.** Never run `python3 -c`,
write a temporary `.py` file to `/tmp`, run Python code on the command line,
or create any other one-off script
to verify that library code behaves correctly. Instead:

1. Write a `test_primary.txt` (or `test_<scenario>.txt`) plan file
   co-located with the library under `libs/<libname>/`.
2. Run it with `bin/dev test` (see `design/testing.md`).

Every behavior check becomes a permanent regression test this way.
The only legitimate use of a scratch script is work completely outside
`libs/` — e.g. a one-off data conversion. If you are tempted to reach for
`python3 -c` to test a library function, write a plan stanza instead.

**Design docs are not read-only.** If a change touches behaviour
the design specifies, update the relevant `design/` file in the
same change. Stale docs are bugs. If you create a new design doc,
mention it in the appropriate top-level or library-owned README.md.

**Write self-documenting code.** Choose function names
to indicate their purpose - not what they do, but what they will be
used for. Choose variable names to indicate the highest-level purpose
of storing the data. If you're tempted to write a comment explaining
the code, look for a way to extract a function with a meaningful name
instead. Long function names like "scan_for_black_pixels" or
"scan_for_line_columns" are fine.

**Refactor mercilessly.** Never write duplicate code in the same
library. Even if it's only a partial line like "foo + bar" write a
function with a name that expresses the purpose of the code, to extract
the functionality and keep it in one place. The only exception is if
the code serves completely different purposes - in that case, put it
in two well-named functions, adjacent to each other in the file, and
call the functions.

## Test harness behaviour to respect

- `bin/dev test --quick` must be green before you stop work. Stubs
  pass quick mode; that is by design.
- `bin/dev test --full` is the real bar. A plan reaching a stub
  halts at that step with a `Stubbed:` line, which counts as
  deferred coverage rather than failure.
- Only run `bin/dev test --learn` with explicit user instruction,
  because it rewrites the `expect` blocks, in effect bypassing the test.

## Verification goes in the test harness — not one-off commands

Any non-trivial "does this work?" check belongs in the harness as a durable,
re-runnable test — never as a throwaway command. This holds for both languages:

- **JS:** add a `web/**/*.test.mjs` (`node:test`/`node:assert`, zero npm deps);
  it runs under `dev test` Domain 3.
- **Python / `libs/`:** add a `test_*.txt` plan next to the library; it runs
  under Domain 1.

Then verify by running `bin/dev test`.

**Do NOT** check behavior with throwaway `node -e '…'`, `python -c '…'`, REPL
snippets, or ad-hoc shell pipelines. **Do NOT** `grep`/eyeball one-off command
output to decide pass/fail — if an assertion is worth making, encode it as a
harness assertion so it's repeatable and gated. The harness's PASS/FAIL is the
source of truth, not a human (or LLM, or grep) reading scrollback.

The only ad-hoc commands allowed are pure *syntax* checks (`node --check`,
`python -m py_compile`) — these verify a file parses, not that it behaves.

## Common mistakes

- Don't skip the stub stage even if the real algorithm seems
  trivial — the stub is what keeps the rest of the system
  end-to-end runnable.
- If you change the music web app's interface, update
  `design/music-web.md` in the same change.
- Don't remove a `@stub` decorator and leave its
  `test_stub_<fn>.txt` behind. The stub-coverage check exits 4 on
  orphan plan files.
