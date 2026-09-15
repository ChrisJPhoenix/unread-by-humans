"""ast_query — side-effect-free AST-based definition lookup and reference search.

Provides Python-`ast`-backed search for function, class, and method
definitions and references across source files. JS/TS/JSX/TSX/MJS files
dispatch to `ast-grep` (read-only subprocess). The public functions are:

- find_definitions(name, paths) -> list[dict]
- definition_line(name, path) -> int
- definition_count(name, path) -> int
- find_references(name, paths) -> list[dict]
- reference_count(name, path) -> int
- outline(paths) -> list[dict]
- outline_count(path) -> int
- outline_names(path) -> str

ast-grep escape hatch (raw structural search):
- ast_grep_path() -> str | None
- ast_grep_available() -> bool
- search_pattern(lang, pattern, paths) -> list[dict]
- pattern_count(lang, pattern, path) -> int

Text grep (pure Python, stdlib re + tokenize):
- grep_text(pattern, paths, *, where, ignore_case) -> list[dict]
- bre_ism_hint(pattern) -> str | None
- directory_notice(dirs_text) -> str
- no_grep_input_message() -> str

Shell-loop replacement (pure Python):
- echo_text(parts) -> str
- find_files(roots, exts, name_regex) -> list[str]
- run_sequence(commands) -> str
- run_foreach(commands, files, placeholder) -> str

Syntax check (pure):
- syntax_check_error(path) -> str | None

CLI testing seam (pure, no print/sys.exit):
- run_cli_argv(argv) -> str
- run_cli_str(cmdline) -> str
- run_cli_with_status(argv) -> tuple[str, int]

Run context (the two channels — see design/look.md §9.1.1):
- new_run_context(log_level, log_repeat) -> dict
- add_log_entry(ctx, level, message) -> None
- run_error_count(ctx) -> int
- render_run_output(data, ctx) -> str
"""
from libs.ast_query.ast_query import (
    find_definitions,
    definition_line,
    definition_count,
    find_references,
    reference_count,
    outline,
    outline_count,
    outline_names,
    syntax_check_error,
    ast_grep_path,
    ast_grep_available,
    search_pattern,
    pattern_count,
    grep_text,
    bre_ism_hint,
    directory_notice,
    no_grep_input_message,
    echo_text,
    find_files,
    run_sequence,
    run_foreach,
    run_cli_argv,
    run_cli_str,
    run_cli_with_status,
    new_run_context,
    add_log_entry,
    run_error_count,
    render_run_output,
)

__all__ = [
    "find_definitions",
    "definition_line",
    "definition_count",
    "find_references",
    "reference_count",
    "outline",
    "outline_count",
    "outline_names",
    "syntax_check_error",
    "ast_grep_path",
    "ast_grep_available",
    "search_pattern",
    "pattern_count",
    "grep_text",
    "bre_ism_hint",
    "directory_notice",
    "no_grep_input_message",
    "echo_text",
    "find_files",
    "run_sequence",
    "run_foreach",
    "run_cli_argv",
    "run_cli_str",
    "run_cli_with_status",
    "new_run_context",
    "add_log_entry",
    "run_error_count",
    "render_run_output",
]
