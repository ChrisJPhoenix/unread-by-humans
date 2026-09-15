"""AST-backed definition lookup for Python and JS/TS source files.

Uses stdlib `ast` for Python (no subprocess). For JS/TS/JSX/TSX/MJS files,
dispatches to `ast-grep` (read-only subprocess; never writes/rewrites files).

ast-grep JSON output uses 0-based line and column numbers; all public
functions present 1-based line numbers (matching Python's ast convention).
The +1 offset was calibrated against fixtures/sample.js: ast-grep reports
line 4 for `function helper(x)` which starts on real line 5.
"""
import argparse
import ast
import fnmatch
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tokenize as _tokenize_module
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# ast-grep backend (JS/TS dispatch)
# ---------------------------------------------------------------------------

_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs"}


def ast_grep_path() -> "str | None":
    """Locate the ast-grep binary: try PATH first, then the venv bin/ directory.

    Returns the absolute path to the binary, or None if it cannot be found.
    NEVER invoke `sg` — on this system /usr/bin/sg is the unrelated newgrp tool.
    """
    on_path = shutil.which("ast-grep")
    if on_path:
        return on_path
    venv_candidate = str(
        __import__("pathlib").Path(sys.executable).parent / "ast-grep"
    )
    if __import__("os").path.isfile(venv_candidate) and __import__("os").access(
        venv_candidate, __import__("os").X_OK
    ):
        return venv_candidate
    return None


def ast_grep_available() -> bool:
    """Return True if the ast-grep binary can be found (via ast_grep_path)."""
    return ast_grep_path() is not None


def search_pattern(lang: str, pattern: str, paths: list[str]) -> list[dict]:
    """Run ast-grep READ-ONLY and return structured match dicts.

    Invocation (0.44 compatible):
        ast-grep run --pattern <pattern> --lang <lang> --json <paths...>

    ast-grep's JSON range.start.line and .column are 0-based; this function
    converts both to 1-based to be consistent with Python's ast convention.
    The +1 offset was calibrated against fixtures/sample.js: ast-grep reports
    line 4 for `function helper(x)` which sits on real 1-based line 5.

    Each returned dict has keys: path, line, col, text.
    Returns [] if the binary is absent, the subprocess fails, or JSON is
    unparseable. Never raises. Never writes any file (no -r/--rewrite flag).

    Args:
        lang:    Language tag (e.g. "js", "ts", "py").
        pattern: Structural pattern in ast-grep syntax (e.g. "function $N($$$) { $$$ }").
        paths:   List of file paths to search.

    Returns:
        List of match dicts sorted by (path, line, col).
    """
    binary = ast_grep_path()
    if binary is None:
        return []
    if not paths:
        return []
    argv = [binary, "run", "--pattern", pattern, "--lang", lang, "--json"] + paths
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if not result.stdout.strip():
        return []
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    matches = []
    for item in raw:
        try:
            matches.append({
                "path": item["file"],
                "line": item["range"]["start"]["line"] + 1,
                "col": item["range"]["start"]["column"] + 1,
                "text": item["text"],
            })
        except (KeyError, TypeError):
            continue
    matches.sort(key=lambda d: (d["path"], d["line"], d["col"]))
    return matches


def pattern_count(lang: str, pattern: str, path: str) -> int:
    """Return the number of ast-grep matches for `pattern` in a single file.

    Args:
        lang:    Language tag (e.g. "js", "ts", "py").
        pattern: Structural pattern in ast-grep syntax.
        path:    Path to a single source file.

    Returns:
        Count of matches (>= 0); 0 if binary absent or file unreadable.
    """
    return len(search_pattern(lang, pattern, [path]))


# ---------------------------------------------------------------------------
# Per-extension JS patterns used by find_definitions / find_references / outline
# ---------------------------------------------------------------------------

_JS_FUNCTION_PATTERN = "function $NAME($$$) { $$$ }"
_JS_CLASS_PATTERN = "class $C { $$$ }"


def _js_function_definitions(path: str, name: str) -> list[dict[str, Any]]:
    """Return ast-grep matches for top-level JS function declarations named `name`."""
    return [
        m for m in search_pattern("js", _JS_FUNCTION_PATTERN, [path])
        if name in m["text"].split("(")[0]
    ]


def _js_class_definitions(path: str, name: str) -> list[dict[str, Any]]:
    """Return ast-grep matches for JS class declarations named `name`."""
    return [
        m for m in search_pattern("js", _JS_CLASS_PATTERN, [path])
        if ("class " + name) in m["text"]
    ]


def _find_js_definitions(name: str, path: str) -> list[dict[str, Any]]:
    """Return definition dicts for `name` in a JS/TS file.

    Searches function declarations and class declarations; each result
    has keys: path, line, col, kind, name.
    """
    results: list[dict[str, Any]] = []
    for m in _js_function_definitions(path, name):
        results.append({
            "path": path,
            "line": m["line"],
            "col": m["col"],
            "kind": "function",
            "name": name,
        })
    for m in _js_class_definitions(path, name):
        results.append({
            "path": path,
            "line": m["line"],
            "col": m["col"],
            "kind": "class",
            "name": name,
        })
    results.sort(key=lambda d: (d["line"], d["col"]))
    return results


def _find_js_references(name: str, path: str) -> list[dict[str, Any]]:
    """Return reference dicts for `name` in a JS/TS file via ast-grep.

    Uses a call-site pattern `name($$$)`. This is a best-effort approximation
    that catches direct calls; aliased/dynamic uses are not detected.
    Each result has keys: path, line, col, name.
    """
    call_pattern = f"{name}($$$)"
    results: list[dict[str, Any]] = []
    for m in search_pattern("js", call_pattern, [path]):
        results.append({
            "path": path,
            "line": m["line"],
            "col": m["col"],
            "name": name,
        })
    results.sort(key=lambda d: (d["line"], d["col"]))
    return results


def _outline_js(path: str) -> list[dict[str, Any]]:
    """Return definition outline for a JS/TS file using ast-grep.

    Detects top-level function declarations and class declarations.
    Each result has keys: path, line, kind, name, end_line.
    end_line is the start line (end-line is not available from JSON range easily
    without further parsing; same-line fallback matches Python's fallback).
    """
    results: list[dict[str, Any]] = []
    for m in search_pattern("js", _JS_FUNCTION_PATTERN, [path]):
        # Extract name between "function " and "("
        try:
            fn_name = m["text"].split("(")[0].split()[-1]
        except IndexError:
            continue
        results.append({
            "path": path,
            "line": m["line"],
            "kind": "function",
            "name": fn_name,
            "end_line": m["line"],
        })
    for m in search_pattern("js", _JS_CLASS_PATTERN, [path]):
        # Extract name between "class " and " {"
        try:
            cls_name = m["text"].split("{")[0].strip().split()[-1]
        except IndexError:
            continue
        results.append({
            "path": path,
            "line": m["line"],
            "kind": "class",
            "name": cls_name,
            "end_line": m["line"],
        })
    results.sort(key=lambda d: d["line"])
    return results


def _is_js_extension(path: str) -> bool:
    """Return True if the file extension marks a JS/TS family file."""
    import os
    return os.path.splitext(path)[1].lower() in _JS_EXTENSIONS


# ---------------------------------------------------------------------------
# Python backend helpers
# ---------------------------------------------------------------------------

def _python_definition_nodes(tree: ast.AST, name: str) -> list[ast.AST]:
    """Return all AST nodes defining `name` (FunctionDef, AsyncFunctionDef, ClassDef).

    Walks the full AST tree, collecting every node whose `.name` attribute
    matches exactly. Returns [] if no matches are found.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, _definition_node_names()) and node.name == name  # type: ignore[union-attr]
    ]


def _node_kind(node: ast.AST) -> str:
    """Return the kind string for a definition node: 'function' or 'class'."""
    if isinstance(node, ast.ClassDef):
        return "class"
    return "function"


def find_definitions(name: str, paths: list[str]) -> list[dict[str, Any]]:
    """Find every definition of `name` across Python and JS/TS source files.

    Dispatches by extension:
    - `.py` → stdlib `ast` parse (no subprocess).
    - `.js/.jsx/.ts/.tsx/.mjs` → ast-grep subprocess (read-only).
    Other extensions are skipped silently.

    Each returned dict has keys: path, line, col, kind, name.
    Results are sorted by (path, line, col).

    Args:
        name: The bare identifier to search for (function, class, or method name).
        paths: Absolute or repo-relative file paths to search.

    Returns:
        List of dicts sorted by (path, line, col).
    """
    results: list[dict[str, Any]] = []
    for path in paths:
        if path.endswith(".py"):
            tree = _parse_python(path)
            if tree is None:
                continue
            for node in _python_definition_nodes(tree, name):
                results.append({
                    "path": path,
                    "line": node.lineno,
                    "col": node.col_offset,
                    "kind": _node_kind(node),
                    "name": name,
                })
        elif _is_js_extension(path):
            results.extend(_find_js_definitions(name, path))
    results.sort(key=lambda d: (d["path"], d["line"], d["col"]))
    return results


def definition_line(name: str, path: str) -> int:
    """Return the line number of the first definition of `name` in `path`.

    Returns 0 if `name` is not defined in `path` or the file cannot be read.

    Args:
        name: Bare identifier (function, class, or method name).
        path: Path to a single Python source file.

    Returns:
        1-based line number, or 0 if not found.
    """
    tree = _parse_python(path)
    if tree is None:
        return 0
    nodes = _python_definition_nodes(tree, name)
    if not nodes:
        return 0
    return min(node.lineno for node in nodes)


def definition_count(name: str, path: str) -> int:
    """Return the number of definitions of `name` in `path`.

    Returns 0 if none are found or the file cannot be read.

    Args:
        name: Bare identifier (function, class, or method name).
        path: Path to a single Python source file.

    Returns:
        Count of definitions (>= 0).
    """
    tree = _parse_python(path)
    if tree is None:
        return 0
    return len(_python_definition_nodes(tree, name))


def _read_source(path: str) -> str:
    """Read a source file and return its text content."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


_BINARY_SNIFF_BYTES = 8192


def _read_text_or_none(path: str) -> "str | None":
    """Read a file as UTF-8 text, or return None if it can't be read as text.

    Returns None when the path can't be opened (OSError — e.g. a directory),
    looks binary (a NUL byte within the first `_BINARY_SNIFF_BYTES`), or is not
    valid UTF-8.  Lets grep walk the whole repo and silently skip non-text files.
    """
    try:
        with open(path, "rb") as fh:
            prefix = fh.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in prefix:
                return None
            rest = fh.read()
    except OSError:
        return None
    try:
        return (prefix + rest).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _parse_python(path: str) -> ast.AST | None:
    """Parse a Python source file and return its AST, or None on failure.

    Returns None if the file cannot be read or contains a syntax error,
    so callers always get a graceful empty result rather than a raised exception.
    """
    try:
        source = _read_source(path)
    except OSError:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def syntax_check_error(path: str) -> "str | None":
    """Syntax-check a single Python file; return None if it parses cleanly.

    Deliberately does NOT reuse `_parse_python`: that helper swallows
    SyntaxError into a bare None so every other query can treat a broken
    file as "no hits" — exactly the detail this function exists to surface.
    A `check` caller needs the exception's line/column/message, not just a
    yes/no.

    Returns:
        None on success. On SyntaxError, a one-line
        "{path}:{line}:{col}: SyntaxError: {msg}" — `col` is 0-based (this
        tool's convention elsewhere) even though `SyntaxError.offset` is
        1-based, so it is converted via `max(offset - 1, 0)`. On OSError
        (missing file, unreadable, a directory), returns
        "{path}: cannot read file".
    """
    try:
        source = _read_source(path)
    except OSError:
        return f"{path}: cannot read file"
    try:
        ast.parse(source)
    except SyntaxError as exc:
        line = exc.lineno or 0
        col = max((exc.offset or 0) - 1, 0)
        return f"{path}:{line}:{col}: SyntaxError: {exc.msg}"
    return None


def _is_reference_to(node: ast.AST, name: str) -> bool:
    """Return True if `node` is a Name or Attribute reference to `name`.

    Counts ast.Name nodes whose id equals name, and ast.Attribute nodes
    whose attr equals name. Does NOT count definition nodes (FunctionDef,
    AsyncFunctionDef, ClassDef).
    """
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Attribute):
        return node.attr == name
    return False


def _definition_node_names() -> tuple[type, ...]:
    """Return the AST node types that constitute a definition."""
    return (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_definition_of(node: ast.AST, name: str) -> bool:
    """Return True if `node` defines `name` (FunctionDef/AsyncFunctionDef/ClassDef)."""
    return isinstance(node, _definition_node_names()) and node.name == name  # type: ignore[union-attr]


def find_references(name: str, paths: list[str]) -> list[dict[str, Any]]:
    """Find every reference/use of `name` across Python and JS/TS source files.

    Dispatches by extension:
    - `.py` → stdlib `ast` (Name/Attribute nodes; definition nodes excluded).
    - `.js/.jsx/.ts/.tsx/.mjs` → ast-grep call-site pattern `name($$$)`.
    Other extensions are skipped silently.

    Each returned dict has keys: path, line, col, name.
    Results are sorted by (path, line, col).

    Args:
        name: The bare identifier to find references for.
        paths: Absolute or repo-relative file paths to search.

    Returns:
        List of dicts sorted by (path, line, col).
    """
    results: list[dict[str, Any]] = []
    for path in paths:
        if path.endswith(".py"):
            tree = _parse_python(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if _is_definition_of(node, name):
                    continue
                if _is_reference_to(node, name):
                    results.append({
                        "path": path,
                        "line": node.lineno,
                        "col": node.col_offset,
                        "name": name,
                    })
        elif _is_js_extension(path):
            results.extend(_find_js_references(name, path))
    results.sort(key=lambda d: (d["path"], d["line"], d["col"]))
    return results


def reference_count(name: str, path: str) -> int:
    """Count references to `name` in `path`, excluding the definition(s).

    Returns 0 if none are found or the file cannot be read.

    Args:
        name: Bare identifier to count references for.
        path: Path to a single Python source file.

    Returns:
        Count of references (>= 0).
    """
    return len(find_references(name, [path]))


def outline(paths: list[str]) -> list[dict[str, Any]]:
    """Return every definition (functions, classes, methods, nested) across files.

    Dispatches by extension:
    - `.py` → stdlib `ast` (FunctionDef, AsyncFunctionDef, ClassDef, all depths).
    - `.js/.jsx/.ts/.tsx/.mjs` → ast-grep patterns for function decls + classes.
      (Methods without the `function` keyword are not detected in the JS path.)
    Other extensions are skipped silently.

    Each returned dict has keys: path, line, kind, name, end_line.
    end_line equals line for JS/TS (ast-grep doesn't expose end-line in the
    JSON range we parse); for Python, end_line is node.end_lineno if available.

    Results are ordered by (path, line).

    Args:
        paths: Absolute or repo-relative file paths to outline.

    Returns:
        List of dicts ordered by (path, line).
    """
    results: list[dict[str, Any]] = []
    for path in paths:
        if path.endswith(".py"):
            tree = _parse_python(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, _definition_node_names()):
                    results.append({
                        "path": path,
                        "line": node.lineno,
                        "kind": _node_kind(node),
                        "name": node.name,  # type: ignore[union-attr]
                        "end_line": getattr(node, "end_lineno", node.lineno),
                    })
        elif _is_js_extension(path):
            results.extend(_outline_js(path))
    results.sort(key=lambda d: (d["path"], d["line"]))
    return results


def outline_count(path: str) -> int:
    """Return the number of definitions in `path`.

    Returns 0 if none are found or the file cannot be read.

    Args:
        path: Path to a single Python source file.

    Returns:
        Count of definitions (>= 0).
    """
    return len(outline([path]))


def outline_names(path: str) -> str:
    """Return a comma-joined string of definition names in source order for `path`.

    Returns an empty string if no definitions are found or the file cannot be read.

    Args:
        path: Path to a single Python source file.

    Returns:
        Comma-separated definition names in source order.
    """
    return ",".join(entry["name"] for entry in outline([path]))


# ---------------------------------------------------------------------------
# DSL tokenize + parse
# ---------------------------------------------------------------------------

# Tokens that are DSL operators (recognized as standalone argv tokens only).
_DSL_OPS: set[str] = {"++", "::", "{{", "}}"}


def _parse_dsl(tokens: list[str]) -> tuple:
    """Parse a DSL token list into an AST of nested tuples.

    Entry point for the recursive-descent parser.  The token list is a flat
    argv (already word-split by the shell); operators are recognized only as
    *standalone* tokens equal to an element of ``_DSL_OPS``.

    Node shapes:
      ``("seq",  [child, …])``  — sequence of pipes joined with ``::``
      ``("pipe", [child, …])``  — pipeline of stages joined with ``++``
      ``("group", child)``       — parenthesised sub-expression ``{{ expr }}``
      ``("loop",  body_node)``   — loop over pipe input; body is the group's inner node
      ``("cmd",  [argv…])``      — leaf command (subcommand + args)

    Single-child ``seq`` / ``pipe`` nodes are collapsed: a lone child is
    returned unwrapped so ``("cmd", …)`` round-trips without a wrapper.

    Args:
        tokens: Flat list of argv tokens (operators and command words/args).

    Returns:
        AST root node as a nested tuple.

    Raises:
        ValueError: On a parse error (unbalanced ``{{``/``}}``, ``loop`` not
            followed by ``{{``, empty stage).
    """
    try:
        node, idx = _parse_expr(tokens, 0)
    except IndexError as exc:
        raise ValueError(f"DSL parse error: unexpected end of tokens") from exc
    return node


def _parse_expr(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse an ``expr`` (= ``seq``) starting at ``idx``; return ``(node, next_idx)``."""
    return _parse_seq(tokens, idx)


def _parse_seq(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``seq`` (``pipe ( '::' pipe )*``); return ``(node, next_idx)``.

    Collects one or more pipe nodes separated by ``::`` at the current nesting
    level (``}}`` terminates without consuming it; it is handled by the group
    caller).  A single child is returned unwrapped.
    """
    children: list[tuple] = []
    child, idx = _parse_pipe(tokens, idx)
    children.append(child)
    while idx < len(tokens) and tokens[idx] == "::":
        idx += 1  # consume '::'
        if idx >= len(tokens) or tokens[idx] in ("::", "++", "}}"):
            raise ValueError(
                f"DSL parse error: empty stage after '::' at position {idx}"
            )
        child, idx = _parse_pipe(tokens, idx)
        children.append(child)
    if len(children) == 1:
        return children[0], idx
    return ("seq", children), idx


def _parse_pipe(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``pipe`` (``stage ( '++' stage )*``); return ``(node, next_idx)``.

    A single child is returned unwrapped.
    """
    children: list[tuple] = []
    child, idx = _parse_stage(tokens, idx)
    children.append(child)
    while idx < len(tokens) and tokens[idx] == "++":
        idx += 1  # consume '++'
        if idx >= len(tokens) or tokens[idx] in ("::", "++", "}}"):
            raise ValueError(
                f"DSL parse error: empty stage after '++' at position {idx}"
            )
        child, idx = _parse_stage(tokens, idx)
        children.append(child)
    if len(children) == 1:
        return children[0], idx
    return ("pipe", children), idx


def _parse_stage(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``stage`` (``group | loop | command``); return ``(node, next_idx)``.

    Dispatches on the current token:
    - ``{{``   → group
    - ``loop`` → loop (must be followed by a group)
    - anything else (and not an operator) → command
    """
    if idx >= len(tokens):
        raise ValueError(f"DSL parse error: expected a stage but reached end of tokens")
    tok = tokens[idx]
    if tok == "{{":
        return _parse_group(tokens, idx)
    if tok == "loop":
        return _parse_loop(tokens, idx)
    if tok in _DSL_OPS:
        raise ValueError(
            f"DSL parse error: expected a stage but found operator {tok!r} at position {idx}"
        )
    return _parse_command(tokens, idx)


def _parse_group(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``group`` (``'{{' expr '}}'``); return ``(("group", inner), next_idx)``.

    Uses balance counting so nested ``{{``/``}}`` pairs are handled correctly.
    """
    assert tokens[idx] == "{{"
    idx += 1  # consume '{{'
    depth = 1
    inner_start = idx
    while idx < len(tokens):
        if tokens[idx] == "{{":
            depth += 1
        elif tokens[idx] == "}}":
            depth -= 1
            if depth == 0:
                break
        idx += 1
    if depth != 0:
        raise ValueError("DSL parse error: unbalanced '{{' — no matching '}}'")
    inner_tokens = tokens[inner_start:idx]
    idx += 1  # consume '}}'
    if not inner_tokens:
        raise ValueError("DSL parse error: empty group '{{ }}'")
    inner_node, inner_idx = _parse_expr(inner_tokens, 0)
    if inner_idx < len(inner_tokens):
        raise ValueError(
            f"DSL parse error: unexpected token {inner_tokens[inner_idx]!r} inside group"
        )
    return ("group", inner_node), idx


def _parse_loop(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``loop`` (``'loop' group``); return ``(("loop", body_node), next_idx)``.

    The loop body is the *inner* node of the required following group (not the
    group wrapper itself).
    """
    assert tokens[idx] == "loop"
    idx += 1  # consume 'loop'
    if idx >= len(tokens) or tokens[idx] != "{{":
        raise ValueError(
            "DSL parse error: 'loop' must be followed by '{{ … }}'"
        )
    group_node, idx = _parse_group(tokens, idx)
    # group_node is ("group", inner); expose the inner as the loop body
    body_node = group_node[1]
    return ("loop", body_node), idx


def _parse_command(tokens: list[str], idx: int) -> "tuple[tuple, int]":
    """Parse a ``command`` (``SUBCMD ARG*``); return ``(("cmd", argv), next_idx)``.

    Consumes tokens up to (but not including) the next operator token
    (``++``, ``::``, ``}}``) or end of the token list.
    """
    argv: list[str] = []
    while idx < len(tokens) and tokens[idx] not in _DSL_OPS:
        argv.append(tokens[idx])
        idx += 1
    if not argv:
        raise ValueError(
            f"DSL parse error: empty command at position {idx}"
        )
    return ("cmd", argv), idx


# ---------------------------------------------------------------------------
# DSL stage classification constants
# ---------------------------------------------------------------------------

# Filters treat piped text as a content stream (grep/head operate on the text lines).
_DSL_FILTERS: set[str] = {"grep", "head"}

# Collectors treat piped text as a newline-delimited list of file paths to operate on.
_DSL_COLLECTORS: set[str] = {"cat", "wc", "outline", "def", "refs", "pattern", "check"}

# Sources ignore pipe input entirely and generate their own output.
_DSL_SOURCES: set[str] = {"ls", "find", "git", "echo", "seq", "each"}


# ---------------------------------------------------------------------------
# Run context: the two channels (see design/look.md §9.1.1, §9.1.2)
# ---------------------------------------------------------------------------

# Levels in threshold order, least verbose first. A threshold admits its own
# level and every level before it: "warning" admits errors and warnings, not info.
_LOG_LEVELS: tuple[str, ...] = ("error", "warning", "info")

_LOG_REPEAT_MODES: tuple[str, ...] = ("collapse", "each")

_LOG_SECTION_SEPARATOR = "---- log ----"


def new_run_context(log_level: str = "warning", log_repeat: str = "collapse") -> dict:
    """Create a fresh run context: the `log` channel plus its rendering config.

    Args:
        log_level: rendering threshold — one of `_LOG_LEVELS` ("error",
            "warning", "info"); see `_entry_passes_threshold`.
        log_repeat: repeat-collapsing mode — one of `_LOG_REPEAT_MODES`
            ("collapse", "each"); see `_rendered_log_entries`.

    Returns:
        A dict `{"log": [], "log_level": log_level, "log_repeat": log_repeat}`.
        A dict, not a class: the harness pins this API by calling module-level
        functions with `arg name = value` and holding the result with
        `save as:`, which a method on a class cannot be.
    """
    return {"log": [], "log_level": log_level, "log_repeat": log_repeat}


def add_log_entry(ctx: dict, level: str, message: str) -> None:
    """Append a (level, message) entry to `ctx`'s log channel. Prints nothing.

    The log channel is appended-to, never consumed, by every stage — it is
    logging infrastructure, not a second data pipe.
    """
    ctx["log"].append((level, message))


def run_error_count(ctx: dict) -> int:
    """Number of `error`-level entries in `ctx`'s log."""
    return sum(1 for level, _ in ctx["log"] if level == "error")


def _entry_passes_threshold(level: str, threshold: str) -> bool:
    """True iff an entry at `level` renders under `threshold`.

    Thresholds order least-to-most-verbose per `_LOG_LEVELS`: a threshold
    admits its own level and every level before it. An unrecognised `level`
    OR an unrecognised `threshold` resolves toward showing MORE, never
    less — a diagnostic hidden by a typo would be exactly the silence this
    whole design exists to remove — so either case returns True.
    """
    if level not in _LOG_LEVELS or threshold not in _LOG_LEVELS:
        return True
    return _LOG_LEVELS.index(level) <= _LOG_LEVELS.index(threshold)


def _format_log_entry(level: str, message: str, occurrences: int) -> str:
    """Render one log entry as text.

    `error` entries are wrapped `**ERROR** <message> **ERROR**`; every other
    level (including an unrecognised one) renders the message unchanged.
    When `occurrences > 1`, ` [N times]` is appended.
    """
    text = f"**ERROR** {message} **ERROR**" if level == "error" else message
    if occurrences > 1:
        text = f"{text} [{occurrences} times]"
    return text


def _rendered_log_entries(ctx: dict) -> list[str]:
    """Render `ctx`'s log to a list of lines, applying threshold + repeat mode.

    Entries failing `_entry_passes_threshold` against `ctx["log_level"]` are
    dropped. Under `ctx["log_repeat"] == "collapse"`, identical
    `(level, message)` pairs render ONCE, at the position of their first
    occurrence, with their total count. Otherwise — "each", or an
    unrecognised mode, which resolves toward showing MORE rather than
    silently collapsing — every occurrence renders, with count 1.
    """
    passing = [
        (level, message)
        for level, message in ctx["log"]
        if _entry_passes_threshold(level, ctx["log_level"])
    ]
    if ctx["log_repeat"] != "collapse":
        return [_format_log_entry(level, message, 1) for level, message in passing]

    counts = Counter(passing)
    seen: set[tuple[str, str]] = set()
    lines = []
    for entry in passing:
        if entry in seen:
            continue
        seen.add(entry)
        level, message = entry
        lines.append(_format_log_entry(level, message, counts[entry]))
    return lines


def render_run_output(data: str, ctx: dict) -> str:
    """Join the `data` and rendered `log` channels into the final CLI output.

    A lone non-empty section prints alone — no separator, no banner. This is
    load-bearing: 64 exact `expect return =` assertions in
    `test_cli.txt`/`test_dsl.txt` depend on an empty log producing exactly
    `data` unchanged. Two non-empty sections join on a
    `_LOG_SECTION_SEPARATOR` line of their own.
    """
    sections = [
        section
        for section in (data, "\n".join(_rendered_log_entries(ctx)))
        if section
    ]
    return f"\n{_LOG_SECTION_SEPARATOR}\n".join(sections)


def _unknown_setting_message(flag: str, value: str, allowed: "tuple[str, ...]") -> str:
    """Return the error text for an unrecognised value of a global log flag."""
    return f"{flag}: unknown value {value!r} (expected one of: {', '.join(allowed)})"


def _apply_log_settings(ctx: dict, log_level: "str | None", log_repeat: "str | None") -> None:
    """Apply the global --log / --log-repeat values to ctx.

    Each of the two settings, when not None, is checked against its allowed
    set (`_LOG_LEVELS` / `_LOG_REPEAT_MODES`). A recognised value is written
    onto `ctx`. An unrecognised value is deliberately NOT written — `ctx`
    keeps its existing (default) setting — and instead becomes an `error`
    log entry, so the bad value reports itself through the very channel it
    configures, rather than triggering an argparse `SystemExit` with the
    top-level usage (design/look.md §9.1.2).
    """
    if log_level is not None:
        if log_level in _LOG_LEVELS:
            ctx["log_level"] = log_level
        else:
            add_log_entry(ctx, "error", _unknown_setting_message("--log", log_level, _LOG_LEVELS))
    if log_repeat is not None:
        if log_repeat in _LOG_REPEAT_MODES:
            ctx["log_repeat"] = log_repeat
        else:
            add_log_entry(ctx, "error", _unknown_setting_message("--log-repeat", log_repeat, _LOG_REPEAT_MODES))


# ---------------------------------------------------------------------------
# CLI — pure entry points (no print, no sys.exit)
# ---------------------------------------------------------------------------

# Directory names pruned from every repo walk.
# venv/api-spec/pw-browsers/audiodata are the dirs
# `bin/work` symlinks into each worktree (large/gitignored build artifacts);
# audiodata now lives at appdata/audiodata and is copied there by bin/work
# (basename-pruned by os.walk, so this set still matches it at its new path).
# .git/node_modules are VCS/deps. SYNC: if bin/work adds or removes a symlink,
# update this set to match (and vice versa) — see bin/work cmd_start.
_SOURCE_SKIP_DIRS = {"venv", ".git", "node_modules", "api-spec", "pw-browsers",
                     "audiodata"}
_SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs"}


def _split_exclude_patterns(values: "list[str] | None") -> "tuple[str, ...]":
    """Normalize a `--exclude` argparse value list into a flat pattern tuple.

    `--exclude a,b` and `--exclude a --exclude b` are equivalent; a trailing
    slash is tolerated so a directory can be named the way it is typed
    (`MCreator/` == `MCreator`). Returns `()` for `None`/empty input.
    """
    if not values:
        return ()
    patterns = []
    for value in values:
        for piece in value.split(","):
            cleaned = piece.strip().rstrip("/")
            if cleaned:
                patterns.append(cleaned)
    return tuple(patterns)


def _path_is_excluded(path: str, excludes: "tuple[str, ...]") -> bool:
    """Return True if `path` matches any `--exclude` glob.

    Each pattern is tried with `fnmatch.fnmatchcase` (case-sensitive on every
    platform) against every path component AND the whole path (both as given
    and `os.path.normpath`-ed), so a bare name like `fixtures` prunes that
    subtree anywhere in the tree while a glob containing a separator or an
    extension (`*/generated/*`, `*.min.js`) matches the full path.
    """
    if not excludes:
        return False
    normalized = os.path.normpath(path)
    candidates = [path, normalized] + [c for c in normalized.split(os.sep) if c and c != "."]
    return any(fnmatch.fnmatchcase(candidate, pattern)
               for pattern in excludes for candidate in candidates)


def _walk_filtered_paths(
    roots: list[str],
    extensions: "set[str] | None",
    name_regex: "str | None",
    excludes: "tuple[str, ...]" = (),
) -> list[str]:
    """Walk each root directory and return files matching extension and name filters.

    Skips `_SOURCE_SKIP_DIRS` at every level. Extension matching is lowercased.
    `name_regex` is applied with `re.search` against the full `os.path.join` path.
    `excludes` (from `--exclude`, see `_path_is_excluded`) prunes matching
    directories from the walk entirely (never descended into) and drops any
    matching file from the results.

    Args:
        roots:      Directory roots to walk (non-empty; caller provides defaults).
        extensions: Set of lowercased dot-prefixed extensions to keep, or None for no filter.
        name_regex: Regex string matched against each path via re.search, or None for no filter.
        excludes:   `--exclude` glob patterns to prune, or `()` for no filter.

    Returns:
        Sorted, deduplicated list of matching file paths.
    """
    results: list[str] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SOURCE_SKIP_DIRS and not _path_is_excluded(os.path.join(dirpath, d), excludes)
            ]
            for fname in filenames:
                if extensions is not None and os.path.splitext(fname)[1].lower() not in extensions:
                    continue
                path = os.path.join(dirpath, fname)
                if name_regex is not None and not re.search(name_regex, path):
                    continue
                if _path_is_excluded(path, excludes):
                    continue
                results.append(path)
    return sorted(set(results))


def _walk_files_with_extensions(paths: list[str], extensions: set[str]) -> list[str]:
    """Expand paths to files matching `extensions`, or walk the repo root if empty.

    If `paths` is non-empty, returns the list as-is (the caller chose explicit
    files; no directory expansion is performed).  If `paths` is empty, walks
    from the current working directory, skipping `_SOURCE_SKIP_DIRS` directories,
    and returns all files whose extension (lowercased) is in `extensions`.
    """
    if paths:
        return paths
    return _walk_filtered_paths(["."], extensions, None)


def _walk_source_files(paths: list[str]) -> list[str]:
    """Expand a list of paths to source files, or walk the repo root if empty.

    If `paths` is non-empty, returns the list as-is (the caller chose explicit
    files; no directory expansion is performed).  If `paths` is empty, walks
    from the current working directory, skipping `venv`, `.git`, and
    `node_modules` directories, and returns all files with a recognised source
    extension.
    """
    return _walk_files_with_extensions(paths, _SOURCE_EXTENSIONS)


def _walk_all_files(paths: list[str]) -> list[str]:
    """Return explicit paths as-is, or walk the cwd for ALL files when empty.

    Grep's default file set: every file under the cwd (skipping
    `_SOURCE_SKIP_DIRS`), regardless of extension.  `grep_text` then skips any
    file that isn't UTF-8 text, so binaries are dropped without an allowlist.
    """
    if paths:
        return paths
    return _walk_filtered_paths(["."], None, None)


def _resolve_walk_paths(
    paths: list[str],
    *,
    recursive: bool,
    extensions: "set[str] | None",
    input_text: "str | None",
    walk_cwd_when_bare: bool,
    excludes: "tuple[str, ...]" = (),
) -> "tuple[list[str], str]":
    """Resolve path operands for a walking subcommand, honoring -r and diagnosing bare dirs.

    Returns (effective_paths, stderr_notice). stderr_notice is "" unless bare
    directory operands were given WITHOUT -r, in which case it is the
    directory_notice() text (one 'PATH: Is a directory' line per dir + a
    single '-r' hint) and those directories are dropped from effective_paths.

    `excludes` (see `_path_is_excluded`) is applied to `paths` FIRST, before
    the dirs/files split, so an explicitly-named excluded file or directory
    operand is rejected outright (like GNU grep) rather than merely being
    excluded from within a walk; an excluded directory is therefore never
    used as a walk root either.

    Path resolution:
      - recursive=True: walk every directory operand (pruning
        _SOURCE_SKIP_DIRS and `excludes`, filtered by `extensions`) unioned
        with explicit file operands; with no operands at all, walk the cwd.
      - recursive=False: keep explicit file operands; directory operands are
        dropped (and reported via the notice). With no operands at all: walk
        the cwd when `walk_cwd_when_bare and input_text is None` (a standalone
        bare invocation's historical default); otherwise return [] (an empty
        pipe supplied no paths, so search nothing).
    """
    had_operands = bool(paths)
    paths = [p for p in paths if not _path_is_excluded(p, excludes)]
    dirs = [p for p in paths if os.path.isdir(p)]
    files = [p for p in paths if not os.path.isdir(p)]
    if recursive:
        if not had_operands:
            return _walk_filtered_paths(["."], extensions, None, excludes), ""
        walked = _walk_filtered_paths(dirs, extensions, None, excludes) if dirs else []
        return sorted(set(files) | set(walked)), ""
    notice = directory_notice("\n".join(dirs))
    if not had_operands:
        if walk_cwd_when_bare and input_text is None:
            return _walk_filtered_paths(["."], extensions, None, excludes), notice
        return [], notice
    return files, notice


def _format_def_hit(d: dict[str, Any]) -> str:
    """Format a definition dict as a CLI output line."""
    return f"{d['path']}:{d['line']}:{d['col']}\t{d['kind']} {d['name']}"


def _format_ref_hit(d: dict[str, Any]) -> str:
    """Format a reference dict as a CLI output line."""
    return f"{d['path']}:{d['line']}:{d['col']}\t{d['name']}"


def _format_outline_hit(d: dict[str, Any]) -> str:
    """Format an outline dict as a CLI output line."""
    return f"{d['path']}:{d['line']}:{d['end_line']}\t{d['kind']} {d['name']}"


def _format_pattern_hit(d: dict[str, Any]) -> str:
    """Format a pattern match dict as a CLI output line."""
    return f"{d['path']}:{d['line']}:{d['col']}\t{d['text']}"


# Token categories understood by grep_text's where= parameter.
_FSTRING_TOKEN_TYPES: frozenset[int] = frozenset(
    v
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
    for v in [getattr(_tokenize_module, name, None)]
    if v is not None
)


def _token_grep_category(tok_type: int) -> str:
    """Map a tokenize token type to a grep_text category label.

    Returns "comments", "strings", or "code".
    """
    if tok_type == _tokenize_module.COMMENT:
        return "comments"
    if tok_type == _tokenize_module.STRING or tok_type in _FSTRING_TOKEN_TYPES:
        return "strings"
    return "code"


def _grep_line_hits(
    path: str,
    source_lines: list[str],
    compiled: "re.Pattern[str]",
) -> list[dict[str, Any]]:
    """Emit one match dict per regex hit in source_lines (plain per-line search).

    Returns hits as dicts with keys: path, line (1-based), col (1-based),
    text (full source line, trailing newline stripped).
    """
    hits: list[dict[str, Any]] = []
    for line_idx, raw_line in enumerate(source_lines):
        line_text = raw_line.rstrip("\n")
        for m in compiled.finditer(line_text):
            hits.append({
                "path": path,
                "line": line_idx + 1,
                "col": m.start() + 1,
                "text": line_text,
            })
    return hits


def _grep_token_hits(
    path: str,
    source: str,
    source_lines: list[str],
    compiled: "re.Pattern[str]",
    where: str,
) -> list[dict[str, Any]]:
    """Emit match dicts for hits within tokens of the requested category.

    Only processes `.py` files (caller is responsible for the extension check).
    Tokenizes `source` and for each token in the selected category runs the
    regex; translates match offsets back to absolute (line, col).  On
    `tokenize.TokenError` or `IndentationError`, returns [].

    Returns hits as dicts with keys: path, line (1-based), col (1-based),
    text (full source line on which the match starts, trailing newline stripped).
    """
    hits: list[dict[str, Any]] = []
    try:
        tokens = list(_tokenize_module.generate_tokens(io.StringIO(source).readline))
    except (_tokenize_module.TokenError, IndentationError):
        return []

    for tok_type, tok_text, tok_start, _tok_end, _line in tokens:
        if _token_grep_category(tok_type) != where:
            continue
        tok_start_line, tok_start_col = tok_start  # 1-based line, 0-based col
        for m in compiled.finditer(tok_text):
            # Count newlines in tok_text before the match to find the source line.
            prefix = tok_text[: m.start()]
            newlines_before = prefix.count("\n")
            if newlines_before:
                # Multi-line token: advance line, reset col relative to last newline.
                abs_line = tok_start_line + newlines_before
                col_in_token = len(prefix) - prefix.rfind("\n") - 1
                abs_col = col_in_token + 1  # 1-based
            else:
                abs_line = tok_start_line
                abs_col = tok_start_col + m.start() + 1  # 1-based
            line_text = source_lines[abs_line - 1].rstrip("\n")
            hits.append({
                "path": path,
                "line": abs_line,
                "col": abs_col,
                "text": line_text,
            })
    return hits


def grep_text(
    pattern: str,
    paths: list[str],
    *,
    where: str = "all",
    ignore_case: bool = False,
) -> list[dict[str, Any]]:
    """Search for a regex pattern in text files and return match location dicts.

    Reads only UTF-8 text files; binary files (NUL-byte sniff in the first
    8 KB) and non-UTF-8 files are silently skipped.  Paths that can't be opened
    (e.g. directories) are also skipped gracefully.

    Args:
        pattern:     Regular expression to search for (stdlib `re` syntax).
                     A bad pattern raises `re.error` — it is not swallowed.
        paths:       List of file paths to search (already expanded by the caller).
        where:       Search scope.  One of:
                       "all"      — plain per-line regex search across every path.
                       "comments" — only within Python COMMENT tokens (.py only).
                       "strings"  — only within Python STRING/FSTRING tokens (.py only).
                       "code"     — only within all other text-bearing tokens (.py only).
                     Non-.py files are silently skipped for "comments"/"strings"/"code".
        ignore_case: When True, compile the pattern with re.IGNORECASE.

    Returns:
        List of match dicts sorted by (path, line, col).  Each dict has keys:
          path  — file path as supplied.
          line  — 1-based line number of the match start.
          col   — 1-based column of the match start.
          text  — full source line on which the match starts (newline stripped).
    """
    flags = re.IGNORECASE if ignore_case else 0
    compiled = re.compile(pattern, flags)  # raises re.error on bad pattern

    hits: list[dict[str, Any]] = []
    for path in paths:
        source = _read_text_or_none(path)
        if source is None:
            continue
        source_lines = source.splitlines(keepends=True)

        if where == "all":
            hits.extend(_grep_line_hits(path, source_lines, compiled))
        else:
            if not path.endswith(".py"):
                continue
            hits.extend(_grep_token_hits(path, source, source_lines, compiled, where))

    hits.sort(key=lambda d: (d["path"], d["line"], d["col"]))
    return hits


def grep_lines(
    pattern: str,
    text: str,
    *,
    where: str = "all",
    ignore_case: bool = False,
) -> str:
    """Search an in-memory text string for a regex and return matching positions.

    Reuses the same where= / ignore_case logic as grep_text but operates on a
    single in-memory string rather than a file.  Used by the DSL filter path
    when a grep stage receives piped text and has no explicit PATH operands.

    Each match is emitted as ``f"{line}:{col}\\t{linetext}"`` (1-based line
    and col; **no path prefix**) — one entry per regex hit per line.

    Args:
        pattern:     Regular expression (stdlib re syntax).  A bad pattern
                     raises re.error — not swallowed.
        text:        In-memory text to search.
        where:       Search scope — same semantics as grep_text: ``"all"``
                     for plain per-line search; ``"comments"``, ``"strings"``,
                     or ``"code"`` to restrict to Python token categories.
                     Non-all scopes treat the text as if it were a .py file.
        ignore_case: When True, compile with re.IGNORECASE.

    Returns:
        Newline-joined ``"{line}:{col}\\t{linetext}"`` strings; empty string
        when there are no matches.
    """
    flags = re.IGNORECASE if ignore_case else 0
    compiled = re.compile(pattern, flags)

    source_lines = text.splitlines(keepends=True)

    if where == "all":
        raw_hits = _grep_line_hits("", source_lines, compiled)
    else:
        raw_hits = _grep_token_hits("", text, source_lines, compiled, where)

    return "\n".join(
        f"{h['line']}:{h['col']}\t{h['text']}"
        for h in raw_hits
    )


# grep/BRE backslash-escapes that Python `re` treats as a LITERAL character,
# mapped to the plain-English role that escape plays in grep/BRE syntax.
_BRE_ESCAPE_ROLES: dict[str, str] = {
    "\\|": "alternation",
    "\\(": "grouping",
    "\\)": "grouping",
    "\\{": "quantifier",
    "\\}": "quantifier",
    "\\+": "quantifier",
    "\\?": "quantifier",
}


def bre_ism_hint(pattern: str) -> "str | None":
    """Return a one-line hint if PATTERN contains a backslash-escaped regex
    operator that grep/BRE treats specially but Python `re` treats as a literal
    character, else None.

    bin/look grep uses Python `re`, where alternation/grouping/quantifiers are
    written bare (`|`, `(`, `)`, `{`, `}`, `+`, `?`) and the backslashed forms
    (`\\|`, `\\(`, ...) match those characters literally. A user reaching for
    grep/BRE syntax (`\\|` for alternation, `\\+` for one-or-more, `\\(...\\)`
    for grouping) will get an unexpected zero-match; this hint names that.
    """
    found = [esc for esc in _BRE_ESCAPE_ROLES if esc in pattern]
    if not found:
        return None
    escaped_list = ", ".join(f"`{esc}`" for esc in found)
    roles = sorted({_BRE_ESCAPE_ROLES[esc] for esc in found})
    role_list = "/".join(roles)
    return (
        f"Note: bin/look grep uses Python regex, not grep/BRE. "
        f"{escaped_list} matched literally here (Python {role_list} is "
        f"written bare: `|` for alternation, `(...)` for grouping, "
        f"`+`/`?`/`{{n}}` for quantifiers) — that may be why this returned "
        f"no matches."
    )


def directory_notice(dirs_text: str) -> str:
    """Return the stderr notice for directory args passed to grep without -r.

    `dirs_text` is a newline-joined list of directory paths (the caller joins
    the offending dirs with "\n"). Returns one `PATH: Is a directory` line per
    non-empty path, followed by a single `For recursive grep use -r` hint line.
    Returns "" when `dirs_text` is empty. The hint is emitted at most once.
    """
    dirs = [d for d in dirs_text.split("\n") if d]
    if not dirs:
        return ""
    lines = [f"{d}: Is a directory" for d in dirs]
    lines.append("For recursive grep use -r")
    return "\n".join(lines)


def no_grep_input_message() -> str:
    """Return the stderr guidance for `grep PATTERN` with no pipe and no path."""
    return (
        "grep: no input. Pipe text in (`… ++ grep PATTERN`), name one or more "
        "files/directories, or use -r to search the current directory recursively."
    )


# ---------------------------------------------------------------------------
# Read-only git subcommand
# ---------------------------------------------------------------------------

# Selection rule: these subcommands are read-only REGARDLESS of any flags
# passed. Mutating-capable subcommands (e.g. branch -D, tag -d, config --add,
# remote, stash, reflog, notes) are deliberately EXCLUDED so the tool keeps
# its never-writes guarantee.
_GIT_READONLY_SUBCOMMANDS: frozenset[str] = frozenset({
    "status", "log", "show", "diff", "blame", "shortlog", "describe",
    "rev-parse", "rev-list", "ls-files", "ls-tree", "cat-file", "name-rev",
    "whatchanged", "show-ref", "merge-base", "diff-tree", "diff-files",
    "diff-index", "cherry", "count-objects", "show-branch", "grep", "var",
})


def run_git(gitargs: list[str]) -> str:
    """Run a read-only git command and return its stdout, with stderr appended on failure.

    Never writes to any file. Only subcommands in _GIT_READONLY_SUBCOMMANDS
    are permitted; mutating subcommands and -C/other-directory access are
    deliberately rejected (cwd + sandbox boundary only).

    Args:
        gitargs: Git argument list starting with the subcommand
                 (e.g. ["rev-parse", "--is-inside-work-tree"]).

    Returns:
        stdout of the git command (stderr appended when exit code is nonzero),
        with trailing newline stripped. Empty string if the git binary is absent
        or an OSError occurs. An error message string if args are invalid.
    """
    if not gitargs:
        return "git: missing subcommand"
    subcmd = gitargs[0]
    if subcmd not in _GIT_READONLY_SUBCOMMANDS:
        return f"git: '{subcmd}' is not an allowed read-only subcommand"
    binary = shutil.which("git")
    if binary is None:
        return ""
    try:
        result = subprocess.run(
            [binary, "--no-pager", *gitargs],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    output = result.stdout
    if result.returncode != 0 and result.stderr:
        output = result.stdout + result.stderr
    return output.rstrip("\n")


def run_wc(wc_args: list[str], paths: list[str]) -> str:
    """Run the system `wc` command and return its output.

    Thin read-only wrapper over the `wc` binary.  Flags and path operands are
    passed through verbatim; no file is written.  When the binary is absent
    returns an empty string.  On nonzero exit, stderr is appended to stdout
    so callers see the error without it being swallowed.

    Args:
        wc_args: Flags to pass directly to `wc` (e.g. ["-l"], ["-w", "-c"]).
        paths:   File paths to pass as operands (may be empty for stdin mode,
                 though stdin is not wired here).

    Returns:
        stdout of `wc` (stderr appended when exit code is nonzero), with the
        trailing newline stripped.  Empty string if the `wc` binary is absent
        or an OSError occurs.
    """
    binary = shutil.which("wc")
    if binary is None:
        return ""
    try:
        result = subprocess.run(
            [binary, *wc_args, *paths],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    output = result.stdout
    if result.returncode != 0 and result.stderr:
        output = result.stdout + result.stderr
    return output.rstrip("\n")


def echo_text(parts: list[str]) -> str:
    """Join parts with a single space and return the result.

    Used as the pure-Python implementation of the `echo` CLI subcommand,
    replacing shell `echo` calls in build scripts and plan stanzas.

    Args:
        parts: Sequence of string tokens to join.

    Returns:
        Single string with all parts joined by " ".
    """
    return " ".join(parts)


def find_files(
    roots: list[str],
    exts: "list[str] | None",
    name_regex: "str | None",
    excludes: "tuple[str, ...]" = (),
) -> list[str]:
    """Walk directories and return files matching optional extension and name filters.

    Args:
        roots:      List of directory paths to walk. Each is walked recursively,
                    skipping `_SOURCE_SKIP_DIRS` (`venv`, `.git`, `node_modules`).
        exts:       List of extensions to keep (e.g. `[".py", "md"]`). Each entry
                    is lowercased and forced to start with a dot, so `"py"` and
                    `".py"` are equivalent. `None` or empty list → no extension filter.
        name_regex: Regex string matched with `re.search` against each full path
                    (as produced by `os.path.join`). `None` → no name filter.
        excludes:   `--exclude` glob patterns to prune, or `()` for no filter.

    Returns:
        Sorted, deduplicated list of matching file paths.
    """
    normalized_exts: "set[str] | None" = None
    if exts:
        normalized_exts = {
            e.lower() if e.startswith(".") else "." + e.lower()
            for e in exts
        }
    return _walk_filtered_paths(roots, normalized_exts, name_regex, excludes)


_CAT_AROUND_DEFAULT = 3


def _line_slice(all_lines: list[str], lines_range: "tuple[int,int] | None", around: "tuple[int,int] | None") -> "tuple[list[str], int]":
    """Return (selected_lines, first_source_line_number) given raw lines and a selection spec.

    Exactly one of lines_range or around may be set; if both are None the whole
    file is returned starting at source line 1.  Source line numbers are 1-based
    and the returned offset is the 1-based number of the first returned line.
    """
    total = len(all_lines)
    if lines_range is not None:
        a, b = lines_range
        a = max(1, a)
        b = min(total, b)
        return all_lines[a - 1: b], a
    if around is not None:
        centre, radius = around
        a = max(1, centre - radius)
        b = min(total, centre + radius)
        return all_lines[a - 1: b], a
    return all_lines, 1


def _number_lines(lines: list[str], first_source_line: int) -> str:
    """Return lines prefixed with 1-based source line numbers in `cat -n` style."""
    numbered = [
        f"{first_source_line + i:>6}\t{line}"
        for i, line in enumerate(lines)
    ]
    return "\n".join(numbered)


def _parse_lines_range(spec: str) -> "tuple[int, int]":
    """Parse an ``A-B`` range spec into a (start, end) tuple of ints (1-based, inclusive).

    Args:
        spec: String of the form ``"A-B"`` where A and B are positive integers.

    Returns:
        (A, B) as integers.

    Raises:
        argparse.ArgumentTypeError: If the spec is not parseable.
    """
    parts = spec.split("-", 1)
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        raise argparse.ArgumentTypeError(f"--lines expects 'A-B', got: {spec!r}")


def _parse_around_spec(spec: str) -> "tuple[int, int]":
    """Parse an ``L`` or ``L:N`` around spec into a (line, radius) tuple of ints.

    When only L is given the radius defaults to ``_CAT_AROUND_DEFAULT``.

    Args:
        spec: String of the form ``"L"`` or ``"L:N"``.

    Returns:
        (L, N) as integers.

    Raises:
        argparse.ArgumentTypeError: If the spec is not parseable.
    """
    parts = spec.split(":", 1)
    try:
        line = int(parts[0])
        radius = int(parts[1]) if len(parts) == 2 else _CAT_AROUND_DEFAULT
        return line, radius
    except ValueError:
        raise argparse.ArgumentTypeError(f"--around expects 'L' or 'L:N', got: {spec!r}")


def head_line_count(requested: int) -> int:
    """Lines to keep for a head count operand, accepting the unix `head -N` spelling.

    argparse's negative-number heuristic binds `-5` to N, and slicing with a raw
    negative bound would drop the LAST 5 lines — the empty string for any shorter
    stream, which reads as "no matches". Unix `head -5` means "first 5", so
    mirror that: the sign is a spelling, not a direction.

    Args:
        requested: The N operand as parsed, which may be negative.

    Returns:
        A non-negative line count.
    """
    return abs(requested)


def head_text_lines(text: str, n: int) -> str:
    """Return the first `n` lines of `text` (split on newline, rejoined with newline).

    Args:
        text: Input text to truncate.
        n:    Number of lines to keep. A negative n is accepted as the unix
              `head -N` spelling of the same positive count.

    Returns:
        First n lines joined by newline, with no trailing newline added.
    """
    return "\n".join(text.split("\n")[:head_line_count(n)])


def head_files(paths: list[str], n: int) -> str:
    """Return the first `n` raw lines of each file, with multi-file ==> banners.

    Unreadable or binary files are skipped silently.  When more than one path is
    given, each file's block is preceded by an ``==> path <==`` banner exactly
    as in `cat_files`.  Lines are NOT numbered.

    Args:
        paths: Ordered list of file paths to read.
        n:     Number of lines to keep per file.

    Returns:
        Raw (unnumbered) first-n-lines string; multiple files separated by a
        blank line between banners.  Empty string when every path is
        unreadable or binary.
    """
    multi = len(paths) > 1
    blocks: list[str] = []
    for path in paths:
        text = _read_text_or_none(path)
        if text is None:
            continue
        body = head_text_lines(text, n)
        if multi:
            blocks.append(f"==> {path} <==\n{body}")
        else:
            blocks.append(body)
    return "\n\n".join(blocks)


def cat_files(
    paths: list[str],
    *,
    lines: "tuple[int,int] | None" = None,
    around: "tuple[int,int] | None" = None,
    number: bool = True,
) -> str:
    """Read one or more files and return their contents, optionally line-sliced and numbered.

    Unreadable or binary files are skipped silently.  When more than one path is
    given each file's block is preceded by an ``==> path <==`` banner.

    Args:
        paths:  Ordered list of file paths to read.
        lines:  Inclusive 1-based (start, end) slice; clamps to file bounds.
        around: (centre_line, radius) window; emits lines ``centre-radius … centre+radius``.
        number: When True (default) prefix every emitted line with its 1-based
                source line number formatted as ``f"{n:>6}\\t{text}"``.

    Returns:
        Formatted string — multiple files are separated by a blank line between
        banners.  Empty string when every path is unreadable / binary.
    """
    multi = len(paths) > 1
    blocks: list[str] = []
    for path in paths:
        text = _read_text_or_none(path)
        if text is None:
            continue
        all_lines = text.splitlines()
        selected, first_line_no = _line_slice(all_lines, lines, around)
        if number:
            body = _number_lines(selected, first_line_no)
        else:
            body = "\n".join(selected)
        if multi:
            blocks.append(f"==> {path} <==\n{body}")
        else:
            blocks.append(body)
    return "\n\n".join(blocks)


_GLOB_METACHARS = frozenset("*?[")


def _path_contains_glob(arg: str) -> bool:
    """Return True if arg contains any glob metacharacter (* ? [)."""
    return any(c in _GLOB_METACHARS for c in arg)


def _entry_kind_char(entry: "os.DirEntry[str]") -> str:
    """Return a single-character kind code for a directory entry: d, f, or l."""
    if entry.is_symlink():
        return "l"
    if entry.is_dir():
        return "d"
    return "f"


def _format_ls_entry(entry: "os.DirEntry[str]", long: bool, base_name: str) -> str:
    """Format one directory entry as a display string.

    Long mode: ``f  12345 name`` (kind, right-aligned size, name).
    Short mode: ``name`` (directory entries get a trailing ``/``).
    """
    kind = _entry_kind_char(entry)
    if long:
        size = entry.stat(follow_symlinks=False).st_size if kind != "d" else 0
        return f"{kind} {size:>8} {base_name}"
    if kind == "d":
        return base_name + "/"
    return base_name


def _ls_entry_excluded(dir_path: str, name: str, excludes: "tuple[str, ...]") -> bool:
    """Return True if the entry `name` inside `dir_path` matches an `--exclude` pattern."""
    return _path_is_excluded(os.path.join(dir_path, name), excludes)


def list_entries(
    args: list[str],
    *,
    long: bool,
    all_: bool,
    recursive: bool,
    excludes: "tuple[str, ...]" = (),
) -> str:
    """List directory entries, optionally recursive, with optional glob filtering.

    Each positional arg is either a glob pattern (contains * ? [) or a
    directory/file path.  Patterns match basenames; paths are listed directly.

    Non-recursive: for each directory arg, list its immediate children (sorted,
    directories suffixed ``/``).  A pattern matches entry basenames in ``.`` via
    ``fnmatch.fnmatch``.

    Recursive (``-r``): ``os.walk`` each directory root (default ``.``), pruning
    ``_SOURCE_SKIP_DIRS``; a pattern matches basenames anywhere, emitting
    relative paths (sorted).

    ``all_=False`` hides dotfiles (basename starts with ``'.'``); ``-a`` shows them.

    Long mode (``-l``): format each row as ``{kind} {size:>8} {name}`` where kind
    is ``d``, ``f``, or ``l`` (symlink); size is ``os.path.getsize`` (0 for dirs).

    Args:
        args:      Positional arguments from the CLI (paths or glob patterns).
                   Defaults to ``["."]`` when empty.
        long:      When True, emit kind+size+name per entry.
        all_:      When True, include dotfile entries.
        recursive: When True, walk subdirectories recursively.
        excludes:  `--exclude` glob patterns to prune, or `()` for no filter.
                   A matching directory is pruned from the walk; an
                   explicitly-named excluded path is rejected too.

    Returns:
        Newline-separated listing string (no trailing newline), or ``""`` if
        nothing matches.
    """
    effective_args = args if args else ["."]

    patterns = [a for a in effective_args if _path_contains_glob(a)]
    plain_paths = [a for a in effective_args if not _path_contains_glob(a)]

    lines: list[str] = []

    if recursive:
        # Patterns match basenames anywhere under walk roots; plain paths are walk roots.
        roots = plain_paths if plain_paths else ["."]
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(
                    d for d in dirnames
                    if d not in _SOURCE_SKIP_DIRS and not _ls_entry_excluded(dirpath, d, excludes)
                )
                all_entries = sorted(filenames + dirnames)
                for name in all_entries:
                    if not all_ and name.startswith("."):
                        continue
                    if patterns and not any(fnmatch.fnmatch(name, p) for p in patterns):
                        continue
                    if _ls_entry_excluded(dirpath, name, excludes):
                        continue
                    full_path = os.path.join(dirpath, name)
                    try:
                        with os.scandir(dirpath) as sd:
                            matching = [e for e in sd if e.name == name]
                    except OSError:
                        continue
                    if not matching:
                        continue
                    entry = matching[0]
                    rel_path = os.path.relpath(full_path, start=".")
                    lines.append(_format_ls_entry(entry, long, rel_path))
    else:
        # Non-recursive: list immediate children of each directory arg, or
        # resolve patterns against entries in the current directory.
        dirs_to_list = plain_paths if plain_paths else ["."]

        for dir_path in dirs_to_list:
            if _path_is_excluded(dir_path, excludes):
                continue
            if os.path.isdir(dir_path):
                try:
                    with os.scandir(dir_path) as it:
                        entries = sorted(it, key=lambda e: e.name)
                except OSError:
                    continue
                for entry in entries:
                    if not all_ and entry.name.startswith("."):
                        continue
                    if patterns and not any(fnmatch.fnmatch(entry.name, p) for p in patterns):
                        continue
                    if _ls_entry_excluded(dir_path, entry.name, excludes):
                        continue
                    lines.append(_format_ls_entry(entry, long, entry.name))
            else:
                # Treat as a single file path.
                parent = os.path.dirname(dir_path) or "."
                base = os.path.basename(dir_path)
                try:
                    with os.scandir(parent) as it:
                        matching = [e for e in it if e.name == base]
                except OSError:
                    continue
                if matching:
                    entry = matching[0]
                    if not all_ and entry.name.startswith("."):
                        continue
                    lines.append(_format_ls_entry(entry, long, entry.name))

        # Apply pattern args against cwd entries when no plain-path dirs were given.
        if patterns and not plain_paths:
            try:
                with os.scandir(".") as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                entries = []
            for entry in entries:
                if not all_ and entry.name.startswith("."):
                    continue
                if _ls_entry_excluded(".", entry.name, excludes):
                    continue
                if any(fnmatch.fnmatch(entry.name, p) for p in patterns):
                    lines.append(_format_ls_entry(entry, long, entry.name))

    return "\n".join(lines)


def _add_exclude_flag(subparser: argparse.ArgumentParser) -> None:
    """Attach the shared --exclude flag to a walking subcommand's parser."""
    subparser.add_argument(
        "--exclude", dest="exclude", action="append", default=None, metavar="PAT",
        help="Prune paths matching PAT from the walk; comma-separated and repeatable.",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the bin/look CLI."""
    parser = argparse.ArgumentParser(
        prog="look",
        description="Side-effect-free AST query tool for Python and JS/TS source files.",
    )
    parser.add_argument(
        "--lang",
        metavar="LANG",
        default=None,
        help="Override extension-based language dispatch (e.g. js, py).",
    )
    parser.add_argument(
        "-H", "--head",
        dest="head",
        type=int,
        default=None,
        metavar="N",
        help="Limit output to the first N lines. "
             "Global flag — place it before the subcommand.",
    )
    parser.add_argument(
        "--log",
        dest="log_level",
        metavar="LEVEL",
        default=None,
        help="Log threshold: error, warning (default), or info. "
             "Global flag — place it before the subcommand.",
    )
    parser.add_argument(
        "--log-repeat",
        dest="log_repeat",
        metavar="MODE",
        default=None,
        help="collapse (default) renders identical log entries once with a count; "
             "each prints every occurrence. Global flag — place it before the subcommand.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_def = sub.add_parser("def", help="Find definitions of NAME.")
    p_def.add_argument("name", metavar="NAME")
    p_def.add_argument("paths", metavar="PATH", nargs="*")
    p_def.add_argument("-r", "--recursive", action="store_true", default=False,
        help="Recurse into directory arguments; with no path, walk the source tree.")
    _add_exclude_flag(p_def)

    p_refs = sub.add_parser("refs", help="Find references to NAME.")
    p_refs.add_argument("name", metavar="NAME")
    p_refs.add_argument("paths", metavar="PATH", nargs="*")
    p_refs.add_argument("-r", "--recursive", action="store_true", default=False,
        help="Recurse into directory arguments; with no path, walk the source tree.")
    _add_exclude_flag(p_refs)

    p_outline = sub.add_parser("outline", help="List all definitions in PATH(s).")
    p_outline.add_argument("paths", metavar="PATH", nargs="+")
    p_outline.add_argument("-r", "--recursive", action="store_true", default=False,
        help="Recurse into directory arguments.")
    _add_exclude_flag(p_outline)

    p_pattern = sub.add_parser("pattern", help="Run a structural ast-grep pattern.")
    p_pattern.add_argument("lang_subcmd", metavar="LANG")
    p_pattern.add_argument("pat", metavar="PAT")
    p_pattern.add_argument("paths", metavar="PATH", nargs="*")
    p_pattern.add_argument("-r", "--recursive", action="store_true", default=False,
        help="Recurse into directory arguments; with no path, walk the source tree.")
    _add_exclude_flag(p_pattern)

    p_grep = sub.add_parser("grep", help="Search files for a regex pattern.")
    p_grep.add_argument("pattern", metavar="PATTERN")
    p_grep.add_argument("paths", metavar="PATH", nargs="*")
    p_grep.add_argument(
        "--in",
        dest="where",
        choices=["comments", "strings", "code", "all"],
        default="all",
        help="Restrict search scope (comments/strings/code require .py files).",
    )
    p_grep.add_argument(
        "-i", "--ignore-case",
        action="store_true",
        default=False,
    )
    p_grep.add_argument(
        "-n", "--line-number",
        action="store_true",
        default=False,
        help="Accepted for grep-compatibility; line numbers are always printed.",
    )
    p_grep.add_argument(
        "-r", "--recursive",
        action="store_true",
        default=False,
        help="Recurse into directory arguments; with no path, search the cwd recursively.",
    )
    _add_exclude_flag(p_grep)

    p_check = sub.add_parser(
        "check", help="Syntax-check Python files; silent on success, error per failure."
    )
    p_check.add_argument("paths", metavar="PATH", nargs="+")

    p_echo = sub.add_parser("echo", help="Join TEXT parts with spaces and return.", prefix_chars="\x00")
    p_echo.add_argument("parts", metavar="TEXT", nargs=argparse.REMAINDER)

    p_git = sub.add_parser("git", help="Run a read-only git command (status/log/show/diff/...).", prefix_chars="\x00")
    p_git.add_argument("gitargs", metavar="ARG", nargs=argparse.REMAINDER)

    p_find = sub.add_parser("find", help="Walk directories and list matching files.")
    p_find.add_argument("roots", metavar="ROOT", nargs="*")
    p_find.add_argument(
        "--ext",
        dest="ext",
        default=None,
        help="Comma-separated extensions, e.g. .py,.md",
    )
    p_find.add_argument(
        "--name",
        dest="name",
        default=None,
        help="Regex matched against each relative path (re.search).",
    )
    _add_exclude_flag(p_find)

    p_cat = sub.add_parser("cat", help="Read a file / line-range / window, line-numbered by default.")
    p_cat.add_argument("paths", metavar="PATH", nargs="+")
    p_cat.add_argument(
        "--lines",
        dest="lines",
        type=_parse_lines_range,
        default=None,
        metavar="A-B",
        help="Inclusive 1-based line range to emit (e.g. 3-7).",
    )
    p_cat.add_argument(
        "--around",
        dest="around",
        type=_parse_around_spec,
        default=None,
        metavar="L[:N]",
        help="Emit lines L-N…L+N (default N=3).  E.g. --around 10 or --around 10:2.",
    )
    p_cat.add_argument(
        "--no-number",
        dest="number",
        action="store_false",
        default=True,
        help="Omit line-number prefixes.",
    )

    p_head = sub.add_parser(
        "head", help="Return the first N raw lines of one or more files (N or unix-style -N)."
    )
    p_head.add_argument("n", metavar="N", type=int, help="Number of lines to keep; may be written N or unix-style -N.")
    p_head.add_argument("paths", metavar="PATH", nargs="*")

    p_ls = sub.add_parser("ls", help="List directory entries, optionally recursive, with glob filtering.")
    p_ls.add_argument("args", metavar="PATH_OR_GLOB", nargs="*")
    p_ls.add_argument("-l", "--long", action="store_true", default=False)
    p_ls.add_argument("-a", "--all", dest="all_", action="store_true", default=False)
    p_ls.add_argument("-r", "--recursive", action="store_true", default=False)
    _add_exclude_flag(p_ls)

    p_wc = sub.add_parser("wc", help="Run the system wc command (line/word/byte counts).", prefix_chars="\x00")
    p_wc.add_argument("wc_args", metavar="ARG", nargs=argparse.REMAINDER)

    p_seq = sub.add_parser("seq", help="Run multiple commands in sequence, joining non-empty outputs.")
    p_seq.add_argument("commands", metavar="CMD", nargs="+")

    p_each = sub.add_parser("each", help="For each file, run command templates with {} substituted.")
    p_each.add_argument("commands", metavar="CMD", nargs="+")
    p_each.add_argument("--files", dest="files", nargs="*", default=None)
    p_each.add_argument("--root", dest="roots", nargs="*", default=None)
    p_each.add_argument("--ext", dest="ext", default=None)
    p_each.add_argument("--name", dest="name", default=None)
    _add_exclude_flag(p_each)

    return parser


def _head_text(text: str, head: "int | None") -> str:
    """Return the first `head` newline-separated lines of `text` (all if head is None)."""
    if head is None or not text:
        return text
    return "\n".join(text.split("\n")[: max(0, head)])



def _dispatch(args: "argparse.Namespace", input_text: "str | None", ctx: dict) -> str:
    """Dispatch a parsed args namespace to the appropriate query function.

    Called by both _run_argv_data (with input_text=None for single-command mode)
    and _run_stage (with a possibly-non-None input_text for the DSL pipe model).
    When input_text is None, behavior is byte-for-byte identical to the pre-C.2
    run_cli_argv dispatch — all existing tests must remain green.

    Args:
        args:       Parsed argparse.Namespace produced by _build_parser().
        input_text: Piped text from the previous DSL stage, or None when
                    invoked in single-command mode (not from a DSL pipe).
        ctx:        The enclosing run's context (see design/look.md §9.1.2);
                    threaded through so `seq`/`each` append to it rather than
                    a log of their own.

    Returns:
        Formatted string output, empty string if no hits.
    """
    sub = args.subcommand

    if sub == "def":
        excludes = _split_exclude_patterns(args.exclude)
        effective_paths, notice = _resolve_walk_paths(
            args.paths, recursive=args.recursive, extensions=_SOURCE_EXTENSIONS,
            input_text=input_text, walk_cwd_when_bare=True, excludes=excludes,
        )
        if notice:
            sys.stderr.write(notice + "\n")
        hits = find_definitions(args.name, effective_paths)
        lines = sorted(_format_def_hit(d) for d in hits)
        return _head_text("\n".join(lines), args.head)

    if sub == "refs":
        excludes = _split_exclude_patterns(args.exclude)
        effective_paths, notice = _resolve_walk_paths(
            args.paths, recursive=args.recursive, extensions=_SOURCE_EXTENSIONS,
            input_text=input_text, walk_cwd_when_bare=True, excludes=excludes,
        )
        if notice:
            sys.stderr.write(notice + "\n")
        hits = find_references(args.name, effective_paths)
        lines = sorted(_format_ref_hit(d) for d in hits)
        return _head_text("\n".join(lines), args.head)

    if sub == "outline":
        excludes = _split_exclude_patterns(args.exclude)
        effective_paths, notice = _resolve_walk_paths(
            args.paths, recursive=args.recursive, extensions=_SOURCE_EXTENSIONS,
            input_text=input_text, walk_cwd_when_bare=False, excludes=excludes,
        )
        if notice:
            sys.stderr.write(notice + "\n")
        hits = outline(effective_paths)
        lines = [_format_outline_hit(d) for d in hits]
        return _head_text("\n".join(lines), args.head)

    if sub == "pattern":
        excludes = _split_exclude_patterns(args.exclude)
        effective_paths, notice = _resolve_walk_paths(
            args.paths, recursive=args.recursive, extensions=_SOURCE_EXTENSIONS,
            input_text=input_text, walk_cwd_when_bare=True, excludes=excludes,
        )
        if notice:
            sys.stderr.write(notice + "\n")
        hits = search_pattern(args.lang_subcmd, args.pat, effective_paths)
        lines = sorted(_format_pattern_hit(d) for d in hits)
        return _head_text("\n".join(lines), args.head)

    if sub == "grep":
        # A piped grep with no explicit path is intercepted upstream in
        # _run_stage (stream mode). Reaching here means we are either NOT piped
        # (input_text is None) or we have explicit path operands.
        excludes = _split_exclude_patterns(args.exclude)
        dirs = [p for p in args.paths if os.path.isdir(p) and not _path_is_excluded(p, excludes)]
        files = [p for p in args.paths if not os.path.isdir(p) and not _path_is_excluded(p, excludes)]
        if args.recursive:
            if not args.paths:
                effective_paths = _walk_filtered_paths(["."], None, None, excludes)
            else:
                walked = _walk_filtered_paths(dirs, None, None, excludes) if dirs else []
                effective_paths = sorted(set(files) | set(walked))
        else:
            notice = directory_notice("\n".join(dirs))
            if notice:
                sys.stderr.write(notice + "\n")
            if not args.paths and input_text is None:
                sys.stderr.write(no_grep_input_message() + "\n")
                return ""
            effective_paths = files
        hits = grep_text(
            args.pattern,
            effective_paths,
            where=args.where,
            ignore_case=args.ignore_case,
        )
        if not hits:
            hint = bre_ism_hint(args.pattern)
            if hint is not None:
                sys.stderr.write(hint + "\n")
        return _head_text("\n".join(_format_pattern_hit(d) for d in hits), args.head)

    if sub == "check":
        for path in args.paths:
            if not path.endswith(".py"):
                add_log_entry(
                    ctx, "warning",
                    f"{path}: not checked — v1 only syntax-checks Python (.py) files",
                )
                continue
            error = syntax_check_error(path)
            if error is not None:
                add_log_entry(ctx, "error", error)
        return ""

    if sub == "echo":
        return _head_text(echo_text(args.parts), args.head)

    if sub == "git":
        return _head_text(run_git(args.gitargs), args.head)

    if sub == "wc":
        return _head_text(run_wc(args.wc_args, []), args.head)

    if sub == "find":
        exts = [p.strip() for p in args.ext.split(",") if p.strip()] if args.ext else None
        excludes = _split_exclude_patterns(args.exclude)
        hits = find_files(args.roots or ["."], exts, args.name, excludes)
        return _head_text("\n".join(hits), args.head)

    if sub == "cat":
        return _head_text(
            cat_files(args.paths, lines=args.lines, around=args.around, number=args.number),
            args.head,
        )

    if sub == "ls":
        excludes = _split_exclude_patterns(args.exclude)
        return _head_text(
            list_entries(
                args.args, long=args.long, all_=args.all_, recursive=args.recursive,
                excludes=excludes,
            ),
            args.head,
        )

    if sub == "seq":
        return _head_text(run_sequence(args.commands, ctx), args.head)

    if sub == "each":
        excludes = _split_exclude_patterns(args.exclude)
        if args.files is not None:
            selected = [f for f in args.files if not _path_is_excluded(f, excludes)]
        else:
            exts = [p.strip() for p in args.ext.split(",") if p.strip()] if args.ext else None
            selected = find_files(args.roots or ["."], exts, args.name, excludes)
        return _head_text(run_foreach(args.commands, selected, ctx=ctx), args.head)

    if sub == "head":
        if args.paths:
            return _head_text(head_files(args.paths, args.n), args.head)
        # DSL filter path: operate on piped input_text when no explicit paths given.
        if input_text is not None:
            return _head_text(head_text_lines(input_text, args.n), args.head)
        return ""

    return ""


def _run_stage(stage_argv: list[str], input_text: "str | None", ctx: dict) -> str:
    """Evaluate a single DSL stage command, threading piped input per §4.4.

    Determines whether the stage is a filter, collector, or source and adjusts
    ``stage_argv`` before parsing so that _dispatch sees the right operands:

    - **Collector** with input_text: appends non-empty lines of input_text as
      additional path positional args (unioned with any already on the stage).
    - **Filter** with input_text is not None (piped input was present at all,
      even an empty stream "") and no explicit PATH: intercepts grep/head
      before dispatch and calls grep_lines / head_text_lines directly. A
      stage with no pipe at all has input_text None and is not intercepted.
    - **Source**: ignores input_text entirely.

    After any argv augmentation, parses with _build_parser() and calls _dispatch.

    Args:
        stage_argv:  Token list for this stage (subcommand + its own args).
        input_text:  Piped text from the previous stage, or None / "" for the
                     first stage in a pipe.
        ctx:         The enclosing run's context, forwarded to _dispatch.

    Returns:
        Stage output string.
    """
    if not stage_argv:
        return ""
    sub = stage_argv[0]
    effective_argv = list(stage_argv)

    if input_text and sub in _DSL_COLLECTORS:
        # Append non-empty piped lines as additional path operands (union with
        # any explicit paths already in stage_argv).
        piped_paths = [line for line in input_text.splitlines() if line.strip()]
        effective_argv = effective_argv + piped_paths

    elif input_text is not None and sub in _DSL_FILTERS:
        # Parse the stage to discover whether it carries any explicit PATH args.
        parser = _build_parser()
        parsed = parser.parse_args(effective_argv)
        has_explicit_path = _stage_has_explicit_path(parsed, sub)
        if not has_explicit_path:
            # Operate on the piped text as a content stream (no file dispatch).
            if sub == "grep":
                return grep_lines(
                    parsed.pattern,
                    input_text,
                    where=parsed.where,
                    ignore_case=parsed.ignore_case,
                )
            if sub == "head":
                return head_text_lines(input_text, parsed.n)
            # Fallthrough for any future filter subcommands.

    # Sources and any un-intercepted case: parse and dispatch normally.
    parser = _build_parser()
    args = parser.parse_args(effective_argv)
    return _dispatch(args, input_text, ctx)


def _stage_has_explicit_path(args: "argparse.Namespace", sub: str) -> bool:
    """Return True if the parsed args for a filter subcommand include at least one explicit path.

    Used by _run_stage to decide whether a filter should operate on piped text
    or on the named files (file wins when explicit paths are present).

    Args:
        args: Parsed Namespace from _build_parser().parse_args(stage_argv).
        sub:  The subcommand name (e.g. "grep", "head").

    Returns:
        True when the subcommand's positional path operand list is non-empty.
    """
    if sub == "grep":
        return bool(getattr(args, "paths", []))
    if sub == "head":
        return bool(getattr(args, "paths", []))
    return False


_PLACEHOLDER_RE = re.compile(
    r"\\([\{\}])"          # group 1: \{ or \} — remove backslash, keep char literal
    r"|(\{\})"             # group 2: {} — innermost placeholder
    r"|(\{([0-9]+)\})",    # group 3+4: {n} — depth-indexed placeholder
)
"""Single-pass regex for placeholder substitution inside loop bodies.

Group 1: a backslash-escaped brace (``\\{`` or ``\\}``); the backslash is removed
         and the brace character is kept as a literal.  This handles both
         ``\\{2}`` and ``\\{2\\}`` forms: each ``\\{`` / ``\\}`` is consumed
         independently, removing its leading backslash.
Group 2: bare ``{}`` — innermost placeholder.
Group 3: ``{n}`` with group 4 = the numeric string for depth-indexed placeholder.
"""


def _subst_placeholders(token: str, loop_stack: tuple) -> str:
    """Replace ``{}``/``{n}`` placeholders in a single token using the current loop stack.

    ``loop_stack[-1]`` is the innermost (current) loop's line.  Placeholders:

    - ``{}``   → ``loop_stack[-1]`` (innermost line).
    - ``{n}`` for ``2 ≤ n ≤ len(loop_stack)`` → ``loop_stack[-n]``
      (so ``{2}`` = one loop out, i.e. ``stack[-2]``).
    - ``{1}`` and any ``{n}`` where ``n > len(loop_stack)`` are left **literal**
      (protects regex quantifiers like ``a{2}`` when outside the active depth).
    - ``\\{`` or ``\\}`` is an escape: the backslash is removed and the brace kept
      literal.  Both ``\\{2}`` and ``\\{2\\}`` yield literal ``{2}``.

    Uses a **single** ``re.sub`` pass (one ``_PLACEHOLDER_RE.sub`` call).

    Args:
        token:      The raw token string to process.
        loop_stack: Tuple of loop-line bindings; index -1 is innermost.

    Returns:
        Token with substitutions applied.
    """
    def _replace(m: "re.Match[str]") -> str:
        if m.group(1) is not None:
            # Backslash-escaped brace: strip the backslash, keep the brace literal.
            return m.group(1)
        if m.group(2) is not None:
            # Bare {} → innermost line.
            return loop_stack[-1]
        # {n} form — group 4 holds the numeric string.
        n = int(m.group(4))
        if n < 2 or n > len(loop_stack):
            # Out-of-range or {1}: leave literal.
            return m.group(3)
        return loop_stack[-n]

    return _PLACEHOLDER_RE.sub(_replace, token)


def _subst_node(node: tuple, loop_stack: tuple) -> tuple:
    """Return a copy of *node* with all ``cmd`` argv tokens substituted via loop_stack.

    Recursively walks ``seq``/``pipe``/``group``/``cmd`` nodes; leaves structure
    intact and only modifies the argv list inside ``("cmd", argv)`` nodes.

    Does **not** recurse into ``("loop", body)`` nodes — a nested loop will
    apply its own substitution when it runs, at which point the stack includes
    both the outer and inner loop lines.  This ensures ``{}`` in a nested
    loop body is resolved by the inner loop (not pre-empted by the outer one).

    Args:
        node:       DSL AST node to process.
        loop_stack: Current loop-line binding stack (innermost last).

    Returns:
        New node tuple with substituted tokens.
    """
    kind = node[0]
    if kind == "cmd":
        return ("cmd", [_subst_placeholders(tok, loop_stack) for tok in node[1]])
    if kind in ("pipe", "seq"):
        return (kind, [_subst_node(child, loop_stack) for child in node[1]])
    if kind == "group":
        return ("group", _subst_node(node[1], loop_stack))
    # "loop" nodes are not recursed into: the inner loop handles its own substitution.
    return node


def _eval_dsl(node: tuple, input_text: "str | None", ctx: dict, loop_stack: tuple = ()) -> str:
    """Evaluate a DSL AST node, threading input_text through the pipe/seq model.

    Node shapes (produced by _parse_dsl):
      ``("cmd",   argv)``        — leaf command; dispatch via _run_stage.
      ``("pipe",  [child, …])``  — fold: each child's output becomes the next
                                   child's input_text; return last child's output.
      ``("seq",   [child, …])``  — each child evaluated with the same outer
                                   input_text; non-empty results joined with "\\n".
      ``("group", child)``       — transparent wrapper; forward input_text.
      ``("loop",  body)``        — per-line iteration with placeholder substitution.

    Args:
        node:        DSL AST tuple from _parse_dsl.
        input_text:  Text arriving from the left of a pipe. At the top level
                     / first stage, input_text is None (not piped at all);
                     downstream pipe stages receive the previous stage's
                     output string, which may be "" (piped-empty). loop/seq
                     still coerce via ``(input_text or "")``, but FILTER
                     routing in _run_stage now distinguishes None (not piped)
                     from "" (piped-empty).
        ctx:         The enclosing run's context (see design/look.md §9.1.2),
                     threaded to every recursive call and stage so diagnostics
                     land in one shared log.
        loop_stack:  Stack of current loop-line bindings, innermost last.
                     Each active ``loop`` appends the current line before recursing.

    Returns:
        Evaluated output string.
    """
    kind = node[0]

    if kind == "cmd":
        argv = node[1]
        return _run_stage(argv, input_text, ctx)

    if kind == "pipe":
        kids = node[1]
        current = input_text
        for kid in kids:
            # A stage that logs a new error produced no usable output, so every
            # downstream stage would be operating on nothing — halt rather than
            # manufacture a second, misleading failure (design/look.md §9.1.1).
            errors_before = run_error_count(ctx)
            current = _eval_dsl(kid, current, ctx, loop_stack)
            if run_error_count(ctx) > errors_before:
                return ""
        return current if current is not None else ""

    if kind == "seq":
        # Unlike `pipe`, `seq` does NOT halt on an error: `::` concatenates
        # independent commands, so a failed element logs its own error while
        # its siblings still run (design/look.md §9.1.1 decision 4). Do not
        # "fix" this to match the pipe's halting behaviour — the asymmetry is
        # deliberate.
        kids = node[1]
        results = []
        for kid in kids:
            out = _eval_dsl(kid, input_text, ctx, loop_stack)
            if out:
                results.append(out)
        return "\n".join(results)

    if kind == "group":
        child = node[1]
        return _eval_dsl(child, input_text, ctx, loop_stack)

    if kind == "loop":
        body = node[1]
        lines = (input_text or "").splitlines()
        results = []
        for line in lines:
            if not line:
                continue
            stack2 = loop_stack + (line,)
            body_subst = _subst_node(body, stack2)
            out = _eval_dsl(body_subst, "", ctx, stack2)
            if out:
                results.append(out)
        return "\n".join(results)

    return ""


def _peel_leading_globals(argv: list[str]) -> "tuple[list[str], dict]":
    """Consume leading -H/--head N, --lang X, --log X, --log-repeat X flags
    before the first non-flag token.

    Only peels flags that appear before the first token that is neither a
    recognised global flag name nor its value.

    Args:
        argv: Full argument list.

    Returns:
        (rest, globals) where rest is the remaining tokens after peeling and
        globals is a dict with keys "head" (int | None), "lang" (str | None),
        "log_level" (str | None), and "log_repeat" (str | None), each None
        when the corresponding flag was absent. A dict rather than a
        five-element tuple: the tuple would be unreadable at the call site,
        and Phase 3 re-cuts this function's shape anyway.
    """
    rest = list(argv)
    head: "int | None" = None
    lang: "str | None" = None
    log_level: "str | None" = None
    log_repeat: "str | None" = None
    while rest:
        tok = rest[0]
        if tok in ("-H", "--head"):
            rest.pop(0)
            if rest:
                head = int(rest.pop(0))
        elif tok == "--lang":
            rest.pop(0)
            if rest:
                lang = rest.pop(0)
        elif tok == "--log":
            rest.pop(0)
            if rest:
                log_level = rest.pop(0)
        elif tok == "--log-repeat":
            rest.pop(0)
            if rest:
                log_repeat = rest.pop(0)
        else:
            break
    return rest, {"head": head, "lang": lang, "log_level": log_level, "log_repeat": log_repeat}


def _run_argv_data(argv: list[str], ctx: dict) -> str:
    """Produce the `data` channel for one argv, appending diagnostics to ctx's log.

    Split out of run_cli_argv (see design/look.md §9.1.2) so `seq`/`each` can
    re-enter the CLI (via run_sequence/run_foreach) with their inner commands'
    diagnostics landing in the *enclosing* run's log, instead of each inner
    command starting — and discarding — a log of its own.

    Subcommands and output formats (one line per hit, joined by newline, no
    trailing newline; empty string when there are no hits):

      def NAME [PATH...]     → "{path}:{line}:{col}\\t{kind} {name}"  sorted
      refs NAME [PATH...]    → "{path}:{line}:{col}\\t{name}"          sorted
      outline PATH [PATH...] → "{path}:{line}:{end_line}\\t{kind} {name}"  source order
      pattern LANG PAT [PATH...] → "{path}:{line}:{col}\\t{text}"     sorted

    Global flags:
      --lang   Override extension-based dispatch (e.g. --lang js).
      -H/--head N  Limit output to the first N lines.
      --log LEVEL  Log threshold: error, warning (default), or info.
      --log-repeat MODE  collapse (default) or each; see design/look.md §9.1.1.

    DSL mode: when any token equals a DSL operator (++ :: {{ }}), the full
    argv is evaluated as a DSL expression.  Leading -H/--head, --lang,
    --log, and --log-repeat flags are peeled before DSL parsing.

    Args:
        argv: Argument list (everything after the program name).
        ctx:  The run context diagnostics are appended to.

    Returns:
        Formatted `data` string, empty string if no hits.
    """
    # DSL recognition: if any standalone token is a DSL operator, evaluate as DSL.
    # Peel leading -H/--head and --lang globals before DSL parsing.
    if any(tok in _DSL_OPS for tok in argv):
        rest, globals_ = _peel_leading_globals(argv)
        # errors_before must be captured, not just `if run_error_count(ctx):` —
        # run_sequence/run_foreach share one ctx across many inner commands,
        # so a bare truthiness check would make every command after the first
        # failure bail spuriously (design/look.md §9.1.1 decision 4).
        errors_before = run_error_count(ctx)
        _apply_log_settings(ctx, globals_["log_level"], globals_["log_repeat"])
        if run_error_count(ctx) > errors_before:
            return ""
        try:
            node = _parse_dsl(rest)
        except ValueError as exc:
            return str(exc)
        return _head_text(_eval_dsl(node, None, ctx), globals_["head"])

    parser = _build_parser()
    args = parser.parse_args(argv)
    # See the DSL branch above for why errors_before (not a bare truthiness
    # check) is required when ctx is shared across seq/each's inner commands.
    errors_before = run_error_count(ctx)
    _apply_log_settings(ctx, args.log_level, args.log_repeat)
    if run_error_count(ctx) > errors_before:
        return ""
    return _dispatch(args, None, ctx)


def run_cli_with_status(argv: list[str]) -> "tuple[str, int]":
    """Run one CLI invocation; return its rendered text and the process exit status.

    Builds a fresh run context, produces the `data` channel via
    _run_argv_data, and renders both channels together. The exit status is
    `1` iff the run logged at least one `error` entry, else `0`.  `__main__.py`
    calls this; `run_cli_argv` is `run_cli_with_status(argv)[0]`.

    Args:
        argv: Argument list (everything after the program name).

    Returns:
        (rendered_output, exit_status).
    """
    ctx = new_run_context()
    data = _run_argv_data(argv, ctx)
    return render_run_output(data, ctx), (1 if run_error_count(ctx) else 0)


def run_cli_argv(argv: list[str]) -> str:
    """Parse argv and dispatch to the appropriate query function; return formatted text.

    Returns the rendered `data` + `log` output (see render_run_output);
    ordinary runs have an empty log and this is exactly the `data` channel.
    Use run_cli_with_status for the variant that also carries the process
    exit status.

    This function never prints and never calls sys.exit.  Only __main__.py
    calls print().  Use run_cli_str for a string-based convenience wrapper.

    Args:
        argv: Argument list (everything after the program name).

    Returns:
        Formatted string output, empty string if no hits.
    """
    return run_cli_with_status(argv)[0]


def run_sequence(commands: list[str], ctx: "dict | None" = None) -> str:
    """Run multiple CLI commands in order and concatenate non-empty outputs.

    Each command string is shell-split and dispatched through _run_argv_data.
    Only non-empty outputs are collected; empty outputs are skipped to avoid
    stray blank lines. Inner commands carry their own flags (e.g. -H).

    Two-faced contract on `ctx` (see design/look.md §9.1.2): when the caller
    passes an enclosing run's `ctx`, this returns only the `data` channel and
    folds every inner command's diagnostics into that shared log — the point
    of the parameter, so an inner failure isn't silently discarded. Called
    without `ctx` (the top-level entry point, e.g. from a plan or the `seq`
    subcommand run standalone), it creates its own context and renders its
    own log via render_run_output.

    Args:
        commands: List of shell-style command strings, e.g. ["echo ---", "grep TODO foo.py"].
        ctx:      The enclosing run's context to fold diagnostics into, or
                  None to run as a standalone top-level entry point.

    Returns:
        Collected non-empty outputs joined by newline, or empty string if all outputs are empty.
    """
    outer = ctx if ctx is not None else new_run_context()
    collected = [out for cmd in commands if (out := _run_argv_data(shlex.split(cmd), outer))]
    data = "\n".join(collected)
    return data if ctx is not None else render_run_output(data, outer)


def run_foreach(commands: list[str], files: list[str], placeholder: str = "{}",
                ctx: "dict | None" = None) -> str:
    """For each file, run all command templates with the placeholder substituted, collect outputs.

    FILE-MAJOR ordering: for each file in files, for each command template in commands,
    substitute every token containing the placeholder, run the command, collect non-empty output.

    Two-faced contract on `ctx` (see design/look.md §9.1.2): when the caller
    passes an enclosing run's `ctx`, this returns only the `data` channel and
    folds every inner command's diagnostics into that shared log — the point
    of the parameter, so an inner failure isn't silently discarded. Called
    without `ctx` (the top-level entry point, e.g. from a plan or the `each`
    subcommand run standalone), it creates its own context and renders its
    own log via render_run_output.

    Args:
        commands:    List of shell-style command template strings. Every token containing
                     `placeholder` (even as a substring) is substituted, so tokens like
                     "=== {} ===" become "=== <file> ===".
        files:       List of file paths to iterate over (in order).
        placeholder: Substitution marker. Defaults to "{}".
        ctx:         The enclosing run's context to fold diagnostics into, or
                     None to run as a standalone top-level entry point.

    Returns:
        Collected non-empty outputs joined by newline, or empty string if all outputs are empty.
    """
    outer = ctx if ctx is not None else new_run_context()
    results = []
    for file in files:
        for template in commands:
            argv = [tok.replace(placeholder, file) for tok in shlex.split(template)]
            out = _run_argv_data(argv, outer)
            if out:
                results.append(out)
    data = "\n".join(results)
    return data if ctx is not None else render_run_output(data, outer)


def run_cli_str(cmdline: str) -> str:
    """Parse a shell-quoted command string and dispatch; return formatted text.

    Convenience wrapper around run_cli_argv that splits via shlex.split.
    Never prints or calls sys.exit.

    Args:
        cmdline: Shell-style command string (e.g. 'def helper libs/ast_query/fixtures/sample.py').

    Returns:
        Formatted string output, empty string if no hits.
    """
    return run_cli_argv(shlex.split(cmdline))
