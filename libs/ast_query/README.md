# ast_query

Side-effect-free AST-query engine backing `bin/look`.

## Purpose

Locate function, class, and method definitions by name across Python
and JS/TS source files, without executing them. Also finds references/uses
of any identifier and produces per-file outlines of all definitions. The tool
also does plain-text and token-aware search (`grep`) to find prose in `.md`
files, Python comments, and string literals — the text the AST modes
deliberately ignore.

## Design

- **Python `.py` files**: stdlib `ast` parse + walk (no subprocess).
- **JS/TS files** (`.js/.jsx/.ts/.tsx/.mjs`): `ast-grep` read-only subprocess
  dispatch. `find_definitions`, `find_references`, and `outline` route
  JS/TS paths through `_find_js_definitions`, `_find_js_references`, and
  `_outline_js` respectively.
- **ast-grep escape hatch**: `search_pattern(lang, pattern, paths)` and
  `pattern_count(lang, pattern, path)` expose raw structural search for
  any supported language.
- **Graceful absence**: if `ast-grep` is not installed, all JS/TS paths
  return `[]`/`0` without raising.
- **grep is pure Python**: `re` for matching, stdlib `tokenize` for `.py`
  comment/string/code segmentation — NO subprocess. This is distinct from
  the structural paths (`def`/`refs`/`outline`/`pattern`) that shell out to
  ast-grep.
- **Zero side effects**: the library never writes or modifies any file
  at runtime. The subprocess never receives `-r`/`--rewrite` flags.
- **1-based line numbers**: `ast-grep` JSON uses 0-based lines; the library
  adds +1 to match Python's `ast` convention throughout.

## Public API

### Definition lookup

| Function | Returns | Description |
|---|---|---|
| `find_definitions(name, paths)` | `list[dict]` | All definitions of `name` across the given files, sorted by (path, line, col). Each dict: path/line/col/kind/name. |
| `definition_line(name, path)` | `int` | Line of the first definition in a single file; 0 if none. |
| `definition_count(name, path)` | `int` | Number of definitions of `name` in a single file. |

`kind` is `"function"` for `FunctionDef`/`AsyncFunctionDef` (including
methods and nested defs) or `"class"` for `ClassDef`.

### Reference / use lookup

| Function | Returns | Description |
|---|---|---|
| `find_references(name, paths)` | `list[dict]` | Every reference/use of `name` across the given files, excluding the definition(s). A reference is an `ast.Name` node with `id == name` OR an `ast.Attribute` node with `attr == name`. Each dict: path/line/col/name. Sorted by (path, line, col). |
| `reference_count(name, path)` | `int` | Count of references to `name` in a single file, excluding the def. |

### Outline

| Function | Returns | Description |
|---|---|---|
| `outline(paths)` | `list[dict]` | Every definition (functions, classes, methods, nested) across the given files in source order. Each dict: path/line/kind/name/end_line. |
| `outline_count(path)` | `int` | Number of definitions in a single file. |
| `outline_names(path)` | `str` | Comma-joined definition names in source order for a single file. |

### ast-grep escape hatch (raw structural search)

| Function | Returns | Description |
|---|---|---|
| `ast_grep_path()` | `str \| None` | Locate the ast-grep binary; `None` if not found. |
| `ast_grep_available()` | `bool` | True if ast-grep binary is present. |
| `search_pattern(lang, pattern, paths)` | `list[dict]` | Run ast-grep read-only; return match dicts (path/line/col/text, 1-based). |
| `pattern_count(lang, pattern, path)` | `int` | Count of structural pattern matches in a single file. |

### Plain-text and token-aware grep

| Function | Returns | Description |
|---|---|---|
| `grep_text(pattern, paths, *, where, ignore_case)` | `list[dict]` | Search for a regex pattern across text files. Returns match dicts (path/line/col/text) sorted by (path, line, col). `where` is one of `"all"`, `"comments"`, `"strings"`, `"code"` (default `"all"`); `ignore_case` applies `re.IGNORECASE`. |
| `bre_ism_hint(pattern)` | `str \| None` | If `pattern` contains a backslash-escaped grep/BRE operator (`\|` `\(` `\)` `\{` `\}` `\+` `\?`) that Python `re` treats as a literal character instead, returns a one-line hint naming the mistake; else `None`. Used by the `grep` subcommand to explain an otherwise-silent zero-match. |

### Shell-loop replacement (pure Python)

| Function | Returns | Description |
|---|---|---|
| `echo_text(parts)` | `str` | Join `parts` with a single space; pure-Python replacement for shell `echo`. |
| `find_files(roots, exts, name_regex)` | `list[str]` | Walk each root directory (skipping `_SOURCE_SKIP_DIRS`), return files matching optional extension list and/or `re.search` name filter. Returns sorted, deduplicated paths. |
| `run_sequence(commands)` | `str` | Shell-split and dispatch each command string through `run_cli_argv`; join non-empty outputs with newline. |
| `run_foreach(commands, files, placeholder='{}')` | `str` | For each file in `files`, substitute `placeholder` in every token of each command template and dispatch through `run_cli_argv`; join non-empty outputs in file-major order. |

### Run context (the two channels)

| Function | Returns | Description |
|---|---|---|
| `new_run_context(log_level, log_repeat)` | `dict` | A fresh run context: `{"log": [], "log_level": ..., "log_repeat": ...}`. |
| `add_log_entry(ctx, level, message)` | `None` | Appends a `(level, message)` entry to `ctx`'s log channel. |
| `run_error_count(ctx)` | `int` | Number of `error`-level entries in `ctx`'s log. |
| `render_run_output(data, ctx)` | `str` | Joins the `data` and rendered `log` channels. A lone non-empty section prints alone (no separator, no banner); two non-empty sections join on a `---- log ----` line. `error` entries render `**ERROR** <message> **ERROR**`; `warning`/`info` render unchanged. |

The two-channel model these primitives implement is specified in the source
monorepo's `look` design document, which is not published here; nothing in
this library calls them yet.

## Fixtures

`fixtures/sample.js` — deterministic JS file for JS backend tests; line 5 = `helper`, line 9 = `greet`, line 13 = `Greeter` class.

`fixtures/sample.py` — a deterministic Python file with known line
numbers, used by `test_primary.txt`. Line numbers are documented in the
file's header comment.

`fixtures/grep_sample.py` — deterministic Python file for grep-mode tests; three `TODO` occurrences at separable lexical positions: comment at line 8 col 3, string literal at line 10 col 15, bare NAME identifier at line 12 col 1.

`fixtures/grep_sample.md` — minimal Markdown fixture for grep-mode tests; `TODO` at line 3 col 1.

`fixtures/noext_sample` — extensionless shell script fixture; `TODO` at line 2 col 3.  Used to verify that grep's `-r` recursive walk returns and reads extensionless UTF-8 files.

## Test plan

`test_primary.txt` — scalar helper assertions against `fixtures/sample.py`.

## CLI

`bin/look` runs `python -m libs.ast_query` with the venv Python. The
underlying dispatch is `run_cli_argv`/`run_cli_str`, which are pure
functions (no print, no sys.exit) for use from the test harness.

### Subcommands

| Subcommand | Usage | Output format (one line per hit) |
|---|---|---|
| `def` | `bin/look def [-r] NAME [PATH...] [--exclude PAT]` | `{path}:{line}:{col}\t{kind} {name}`, sorted |
| `refs` | `bin/look refs [-r] NAME [PATH...] [--exclude PAT]` | `{path}:{line}:{col}\t{name}`, sorted |
| `outline` | `bin/look outline [-r] PATH [PATH...] [--exclude PAT]` | `{path}:{line}:{end_line}\t{kind} {name}`, source order |
| `pattern` | `bin/look pattern [-r] LANG PAT [PATH...] [--exclude PAT]` | `{path}:{line}:{col}\t{text}`, sorted |
| `grep` | `bin/look grep PATTERN [PATH...] [--exclude PAT]` | `{path}:{line}:{col}\t{matched line text}`, sorted by (path, line, col) |
| `check` | `bin/look check PATH...` | Nothing on success; `**ERROR** path:line:col: SyntaxError: msg **ERROR**` per failing file, exit 1 |
| `echo` | `bin/look echo TEXT...` | Parts joined by space; dash-prefixed tokens like `---` are captured literally. |
| `find` | `bin/look find [ROOT...] [--ext .py,.md] [--name REGEX] [--exclude PAT]` | One matching path per line, sorted. Defaults to `"."` when no ROOT given. |
| `seq` | `bin/look seq 'CMD' 'CMD' ...` | Runs each quoted sub-command; joins non-empty outputs with newline. |
| `each` | `bin/look each 'CMD' 'CMD' ... [--files ... \| --root ... --ext ... --name ...] [--exclude PAT]` | For each selected file × each command template (`{}` → file path), joins non-empty outputs file-major. |
| `git` | `bin/look git SUBCMD [ARGS...]` | stdout of the read-only git command (stderr appended on failure). |
| `cat` | `bin/look cat PATH... [--lines A-B] [--around L[:N]] [--no-number]` | File contents, line-numbered by default; `==> path <==` banner per file when multiple PATHs given. |
| `ls` | `bin/look ls [-l] [-a] [-r] [PATH_OR_GLOB...] [--exclude PAT]` | Entry names (or `kind size name` with `-l`), directories suffixed `/`; one per line, sorted. |
| `head` | `bin/look head N [PATH...]` | First `N` raw lines of the file(s), or of piped text when used as a DSL filter. Count may be written `N` or unix-style `-N`; both mean "first N". |
| `wc` | `bin/look wc [WC_FLAGS...] [PATH...]` | Output of the system `wc` command; flags pass through verbatim. |

Lines are joined by `"\n"` with no trailing newline. Empty string when there
are no hits. `PATH` defaults to a repo-root walk (skipping `venv`, `.git`,
`node_modules`, `api-spec`, `pw-browsers`) when omitted — tests always pass
explicit paths.

For `def`/`refs`/`outline`/`pattern`, the default walk uses source extensions
(`.py .js .jsx .ts .tsx .mjs`). All four now accept `-r`/`--recursive`
(mirroring `grep`): a bare directory operand WITHOUT `-r` is reported on
stderr (`PATH: Is a directory` plus a one-line `-r` hint) and dropped rather
than silently walked; with `-r`, directory operands are walked recursively
(pruning `_SOURCE_SKIP_DIRS`, filtered to source extensions). A standalone
bare `def`/`refs`/`pattern` invocation (no path, no pipe) still defaults to
walking the cwd source tree, as before; `outline` already requires an
explicit `PATH` (`nargs="+"`), so it has no bare-invocation case. Piped input
is never re-walked as a whole-cwd fallback: an explicitly-empty piped stream
(as opposed to no pipe at all) means "no paths — search nothing".

`grep` behaves like real `grep`: a bare
directory operand without `-r` is an error (`PATH: Is a directory` on
stderr, plus a one-line `-r` hint) rather than a silent skip; `grep PATTERN`
with no pipe and no path operand is a guidance error on stderr instead of an
implicit whole-cwd walk. `grep_text` reads only UTF-8 text: binary files
(NUL-byte sniff in the first 8 KB) and non-UTF-8 files are silently skipped.

#### def/refs/outline/pattern-specific flags

`-r` / `--recursive` — recurse into any directory operand (skipping
`_SOURCE_SKIP_DIRS`, filtered to source extensions); with `-r` and no path
operand at all (`def`/`refs`/`pattern` only — `outline` requires a path),
recursively walks the cwd.

#### grep-specific flags

`-r` / `--recursive` — recurse into any directory operand (skipping
`_SOURCE_SKIP_DIRS`), regardless of extension; with `-r` and no path operand
at all, recursively searches the cwd.

`--in {comments,strings,code,all}` (default `all`) — restrict the search scope:

- `all`: plain whole-line regex search across every matched file regardless of
  extension.
- `comments`: for `.py` files, match only within `COMMENT` tokens; non-`.py`
  files are skipped entirely.
- `strings`: for `.py` files, match only within `STRING`/`FSTRING` tokens;
  non-`.py` files are skipped entirely.
- `code`: for `.py` files, match only within all other text-bearing tokens;
  non-`.py` files are skipped entirely.

`-i` / `--ignore-case` — compile the pattern with `re.IGNORECASE`.

`col` in every hit is the **1-based** column of the match start within the
line.

#### check-specific notes

`check` is **Python-only in v1**: each PATH must end in `.py`, or it draws a
plain (unwrapped) warning naming the path and is skipped rather than checked
— non-Python files (including JS/TS) are not attempted. On success `check`
prints nothing; a syntax error renders wrapped in `**ERROR**` (see [Log
channel](#log-channel)), which is what drives the non-zero exit. `check` is
a DSL **collector** (see the Stage classification table below): piped file
paths become PATH operands, so `find … --ext .py ++ check` syntax-checks
every walked file.

`--lang LANG` and `-H N`/`--head N` are **global** flags and must precede the
subcommand.

#### --exclude (walking subcommands)

`--exclude PAT` — on all eight walking subcommands: `def`/`refs`/`outline`/
`pattern`/`grep`/`ls`/`find`/`each`. Comma-separated and repeatable:
`--exclude a,b` is equivalent to `--exclude a --exclude b`. A trailing slash
is tolerated (`--exclude MCreator/` == `--exclude MCreator`).

Each pattern is an `fnmatch.fnmatchcase` glob (case-sensitive on every
platform), matched against ANY path component OR the full path — so
`--exclude fixtures` means "nothing under a `fixtures` dir, ever", while
`--exclude '*/generated/*'` or `--exclude '*.min.js'` match the full path. A
matching directory is **pruned** from the walk (never descended into), not
merely filtered out of the output. An explicitly-named excluded path is
rejected too, like GNU grep: `outline PATH --exclude PAT` where `PATH`'s
basename matches `PAT` outputs nothing. `--exclude` is additive to the
always-skipped `_SOURCE_SKIP_DIRS`; there is no `--include`. It is
per-subcommand (like `-r`), not global — repeat it on each DSL stage that
needs it.

`--exclude` is deliberately absent from `cat`/`head`/`wc`/`echo`/`seq`
(they take explicit operands and never walk) and from `git` (which has its
own `:(exclude)` pathspec syntax).

#### cat-specific flags

`--lines A-B` — emit an inclusive 1-based line range (e.g. `--lines 3-7`).
`--around L[:N]` — emit the `2N+1` lines centred on line `L` (N defaults to
3). `--no-number` — omit line-number prefixes (useful mid-pipe so a downstream
filter sees raw content). Multiple PATHs concatenate with a `==> path <==`
banner per file. Binary and non-UTF-8 paths are skipped gracefully.

#### ls-specific flags

`-r` / `--recursive` — recurse from each directory root, pruning
`_SOURCE_SKIP_DIRS`. A positional containing glob metachars (`*`, `?`, `[`)
is treated as a **basename pattern** (quote it so bash doesn't expand it:
`bin/look ls -r "*.py"`); otherwise it is a directory/file path. `-l` — long
form (kind + size + name). `-a` — include dotfiles (hidden by default).

#### Meta-command semantics

`echo`, `find`, `seq`, and `each` are PURE Python (no subprocess, no `sed`/`awk`), unlike the ast-grep-backed `pattern`/`def`/`refs`/`outline` paths and the subprocess-backed `git` and `wc` paths.

##### `echo`

Joins all positional TEXT arguments with a single space and returns the result. Dash-prefixed tokens (e.g. `---`) are captured literally because the subparser uses `prefix_chars="\x00"`.

##### `find`

Walks each ROOT directory recursively (default `.` when no ROOT is given), skipping `_SOURCE_SKIP_DIRS` (`venv`, `.git`, `node_modules`, `api-spec`, `pw-browsers`) at every level. The last three plus `venv` mirror the dirs the source monorepo's worktree tooling (not published in this repository) symlinks into a worktree — see the SYNC comment on `_SOURCE_SKIP_DIRS`.

- `--ext` accepts a comma-separated list of extensions (e.g. `.py,.md`). Each entry is lowercased and a leading dot is inserted if absent, so `py` and `.py` are equivalent.
- `--name` is a regex applied with `re.search` against the full joined path (as produced by `os.path.join`). Matches anywhere in the path, not just the filename.

Returns a sorted, deduplicated list of matching paths, one per line.

##### `seq`

Each positional argument is one shell-quoted sub-command string, dispatched through the same `run_cli_argv` entry point. Non-empty outputs are collected and joined with newline; empty outputs are filtered so leaf commands with no hits do not leave blank lines.

The global `-H N` flag is NOT meaningful on `seq` (the top-level output is already a concatenated string). Place `-H N` inside each inner command string if needed.

##### `each`

For each selected file, runs every command template with the `{}` placeholder substituted by the file path. The `{}` placeholder is substituted in EVERY token of each command template — both whole-token and substring-in-token substitution work (e.g. the token `=== {} ===` becomes `=== <file> ===`).

File-selection precedence: `--files` (used verbatim, even if the list is empty) takes priority over the find-style `--root`/`--ext`/`--name` selectors. When `--files` is absent, `find_files` is called with the root/ext/name parameters.

Output ordering is file-major: all commands for file 1 are collected before file 2, and so on. Empty outputs are filtered. The global `-H N` flag is not meaningful on `each`; inner commands carry their own flags.

##### Read-only git

`git` is a SUBPROCESS path (like `pattern`/`def`/`refs`/`outline` via ast-grep), DISTINCT from the pure-Python `echo`/`find`/`seq`/`each` meta-commands.

**Allowed subcommands** (read-only regardless of any flags passed):

`status`, `log`, `show`, `diff`, `blame`, `shortlog`, `describe`, `rev-parse`, `rev-list`, `ls-files`, `ls-tree`, `cat-file`, `name-rev`, `whatchanged`, `show-ref`, `merge-base`, `diff-tree`, `diff-files`, `diff-index`, `cherry`, `count-objects`, `show-branch`, `grep`, `var`.

**Selection rule**: these subcommands are read-only REGARDLESS of any flags passed. Mutating-capable subcommands (e.g. `branch -D`, `tag -d`, `config --add`, `remote`, `stash`, `reflog`, `notes`) are deliberately EXCLUDED so the tool keeps its never-writes guarantee.

**Restrictions**: `-C`/other-directory access is deliberately excluded — the cwd and the sandbox boundary are the only scope. Output is git stdout with stderr appended on a nonzero exit. `--no-pager` is always passed to avoid any pager in a non-tty.

Composes with `seq`/`echo`/`-H` exactly like other subcommands:

```
bin/look seq 'git status' 'echo === LAST COMMITS ===' 'git log --oneline -6'
```

```
bin/look seq '-H 40 git show --stat <sha>' 'echo =========================' '-H 40 git show --stat <sha2>'
```

`-H N` is a **global** flag and must come BEFORE the `git` subcommand (inside each `seq` string, the inner string is re-parsed from scratch, so the global flag still leads). Placing `-H N` after `git` causes it to be captured by git's own `nargs=REMAINDER` argument list and passed to the `git` binary — which has no such flag — so it is silently ignored instead of limiting output.

#### Shell-loop replacement example

```
bin/look each 'echo === Scanning {} ===' 'grep foo {}' --ext .py --root libs/ast_query/fixtures
```

This replaces the un-auto-approvable shell loop:

```sh
for f in $(find libs/ast_query/fixtures -name '*.py'); do echo "=== Scanning $f ==="; grep foo "$f"; done
```

One allowlisted `bin/look each` invocation instead of an open-ended shell loop with subprocesses.

### Global flags

| Flag | Effect |
|---|---|
| `--lang LANG` | Override extension-based language dispatch (e.g. `--lang js`). |
| `--head N` / `-H N` | Limit output to the first N lines. Global — place it before the subcommand. On `seq`/`each` it caps the final joined output's lines. `-h` remains argparse's built-in help. |
| `--log LEVEL` | Log threshold: `error`, `warning` (default), or `info`. Global — place it before the subcommand. An unrecognised value logs an `**ERROR**` and suppresses the data channel; see [Log channel](#log-channel). |
| `--log-repeat MODE` | `collapse` (default) renders identical log entries once with a count; `each` prints every occurrence. Global — place it before the subcommand. |

### Log channel

`bin/look` renders two channels: `data` (what `++` moves between stages) and
`log` (diagnostics appended by stages, never consumed). Log entries carry a
level: `error` prints `**ERROR** … **ERROR**` and halts the `++` pipe at that
stage and makes the process exit non-zero; `warning` prints plain and lets
the run continue; `info` does not print by default. `--log LEVEL` sets the
rendering threshold; `--log-repeat MODE` controls whether identical entries
collapse to one line with a count (`collapse`, the default) or each render
separately (`each`). A run with only one non-empty channel prints that
channel alone, unbannered; when both `data` and `log` are non-empty they are
joined by a `---- log ----` separator line. (Spec: the source monorepo's
`look` design document, not published here.)

### Testing seam

`run_cli_argv(argv: list[str]) -> str` and `run_cli_str(cmdline: str) -> str`
are the pure, harness-testable entry points. `run_cli_str` is a convenience
wrapper that splits via `shlex.split`. Both are exported from
`libs.ast_query` and tested in `test_cli.txt` (full mode).

---

## DSL (chaining inside one call)

The DSL lets multiple stages run inside a single `bin/look` invocation — no
bash pipeline, no subprocess-of-subprocess.

### Operators (standalone argv tokens)

Operators are recognized **only as standalone argv tokens** (never as
substrings), so a regex like `[a-z]{2}` or a pattern like `makeButton\(` in
a stage argument is untouched.

| Token | Role |
|---|---|
| `++` | **Pipe** — feed the output of the left stage as input to the right stage. |
| `::` | **Sequence** — evaluate both sides with the same outer input; join non-empty results with `"\n"`. Lower precedence than `++`. |
| `{{` / `}}` | **Group** — explicitly control precedence; nest by balance-counting standalone `{{`/`}}` tokens. `{{`/`}}` survive bash bare (they have neither a comma nor a `..` range, so bash brace-expansion never fires). |
| `loop` | **Iterator** — `loop {{ BODY }}` runs BODY once per non-empty line of pipe input; `{}` in BODY is substituted with the current line. |

Leading global flags (`-H N`, `--lang`, `--log`, `--log-repeat`) apply to the whole DSL result.

### Stage classification

When text arrives via `++`, each stage type interprets it differently:

| Class | Subcommands | Pipe-input treatment |
|---|---|---|
| **Filters** | `grep`, `head` | Treat piped text as a **content stream** — process its lines directly (like a bash `\| grep`/`\| head`). Never re-interpret piped lines as file paths. |
| **Collectors** | `cat`, `wc`, `outline`, `def`, `refs`, `pattern`, `check` | Treat piped text as a **newline list of file paths**, union with any explicit PATH operands, and operate on those files. |
| **Sources** | `ls`, `find`, `git`, `echo`, `seq`, `each` | **Ignore** pipe input entirely; generate their own output. |
| **Iterator** | `loop {{ BODY }}` | Split piped text into non-empty lines; substitute `{}`→line into BODY and evaluate once per line; concatenate per-line results. |

`{}` is the current (innermost) loop line. `{2}` is one loop out, `{3}` two
out, etc. There is deliberately **no `{1}`** — it would duplicate `{}` and
invite off-by-one bugs. Any `{n}` outside the active nesting depth, and the
token `{1}`, are left **literal** (not substituted, not an error), so ordinary
regex quantifiers like `a{2}` or `[0-9]{3}` survive whenever they fall outside
the active loop depth. Inside a loop nested `D` deep, a literal counted-
repetition quantifier whose count falls within `{2}`…`{D}` must be written
`\{N\}` to avoid substitution.

There is no bash stdin into `bin/look` — text flows only through `++` within
one invocation.

### Worked examples

**Count lines in every Python file under the current directory:**

```
bin/look ls -r "*.py" ++ wc -l
```

`ls -r "*.py"` is a Source (emits a path-per-line list); `wc` is a Collector
(receives those paths as file operands) — equivalent to `wc -l $(find . -name '*.py')`.

**Grep a JS file case-insensitively, take the first 40 hits, then append its outline:**

```
bin/look grep '>Extract|makeButton\(' web/static/pluck_workbench.js -i ++ head 40 :: outline web/static/pluck_workbench.js
```

The `++` pipe feeds grep hits into the `head` filter. The `::` sequence then
appends the file's outline. One process, no shell, auto-approved.

**Per-file banner + TODO grep across all Python files:**

```
bin/look ls -r "*.py" ++ loop {{ echo "=== {} ===" :: cat {} ++ grep TODO }}
```

`ls -r "*.py"` is a Source; `loop` iterates its lines, substituting `{}`
with each path. Inside the loop body, `echo "=== {} ==="` prints a banner,
`::` sequences it with `cat {} ++ grep TODO` (Collector `cat` reads the file,
Filter `grep` finds TODO lines).

---

## Test plans

`test_primary.txt` — scalar helper assertions against `fixtures/sample.py`, plus the run-context/rendering primitives (quick mode).

`test_jsbackend.txt` — JS backend pattern/class pattern counts (full mode).

`test_cli.txt` — end-to-end CLI formatting assertions via `run_cli_str` (full mode); covers `def`/`refs`/`outline`/`pattern`/`grep` (including `--in` modes against `fixtures/grep_sample.py` and `fixtures/grep_sample.md`), `--exclude` on those same five searching subcommands (directory pruning, an explicitly-named excluded path being rejected, a full-path glob, the comma-separated and repeated-flag forms being equivalent, and a tolerated trailing slash) plus the three walking subcommands `ls`/`find`/`each` (directory pruning, the explicitly-named-path rejection, the comma/repeated-flag equivalence, the tolerated trailing slash, and `each --files` honouring `--exclude`), all four meta-commands: `echo`, `find`, `seq`, and `each` (both `--files` and `--root`/`--ext` selectors), the `git` subcommand (allowlist enforcement, error messages, `-H` composition, `seq` composition), the new subcommands `cat` (range, around, multi-file banner, `--no-number`), `ls` (names, `-l`, `-a`, default `.`, `-r` glob), `head` (file head), `wc` (skipped/normalized if `wc` binary semantics vary), the global `--log`/`--log-repeat` flags (recognised values leave the data channel unchanged; an unrecognised value renders as `**ERROR**` and suppresses data), and `check` (clean-file silence, a `SyntaxError` rendered `**ERROR**`, several paths with one bad among them, a non-`.py` path drawing a plain warning, and the `find … ++ check` DSL collector path).

`test_dsl.txt` — end-to-end DSL expression assertions via `run_cli_str` (full mode); covers filter pipe (`grep … ++ head N`), collector pipe (`ls -r "*.py" ++ outline` / `… ++ cat ++ grep`), `::` concatenation, `{{ }}` grouping + precedence, `loop` (`{}` substitution + per-line concatenation; empty input → empty; nested `loop` with `{2}` and literal out-of-range `{2}`/`{1}` left untouched), source stages ignoring pipe input, unbalanced-brace parse error, backward-compat (no operator → single subcommand unchanged), leading `-H` cap, and leading `--log`/`--log-repeat` (recognised values pass through unchanged; an unrecognised value is caught before DSL parsing).

`test_gaps.txt` — full mode; pins CURRENTLY-WRONG `bin/look` behaviour registered in the source monorepo's `look` design document's gap register (not published here), using the fixture `gap_fixtures/const_sample.py`. Each stanza is tagged with its `LG-n` id and carries a `# DESIRED:` comment; closing a gap means flipping the `expect` and deleting the tag.

## Keeping the docs in sync

`bin/look`'s user-facing surface is documented in TWO places that must change
**together** whenever a subcommand or flag is added, removed, or renamed:

1. **This README** (in-repo) — the Public API tables, the Subcommands table,
   the flag sections, and the DSL section above.
2. **The `look` skill** at `~/.claude/skills/look/SKILL.md` — its command list
   AND its YAML frontmatter `description` (the trigger text that decides when
   the skill loads; keep its three capability groups — structural / text /
   compose — current). The design document for the full `look` surface lives
   in the source monorepo and is not published here.

The skill file lives OUTSIDE the repo and the sandbox, so a `my-worker` cannot
edit it: update this README inside the change, then update `SKILL.md` from the
top level. A `bin/look` surface change is not done until BOTH are current.

## `bin/look` shim

`bin/look` runs `python -m libs.ast_query` with the venv Python and the
repo root on `PYTHONPATH`. No design doc is needed for this dev tool;
this README is the complete reference.
