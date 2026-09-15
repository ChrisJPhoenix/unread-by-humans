"""music_scores — score-filename validation and session persistence helpers.

Pure string/list/int logic for the `/music` web port's file layer
(`design/music-web-files.md`). No Flask, no filesystem, no imports from
`music/`. Structured (list/dict) boundary values are carried as JSON strings
so every function is directly callable from the plan-file test harness,
which has no list/dict literal syntax (`design/testing.md` §Plan-file
format) — the same convention `libs/music_web_instruments` uses for its
JSON-string arguments.
"""
from __future__ import annotations

import json
from typing import Optional

_SCORES_DIR_REL = "appdata/webmusicdata/scores"
_SCORE_EXTENSIONS = (".music", ".txt")


def _has_score_extension(name: str) -> bool:
    """True iff `name` ends in one of the accepted score extensions."""
    return name.endswith(_SCORE_EXTENSIONS)


def is_valid_score_name(name: str) -> bool:
    """True iff `name` is a bare, non-traversing score filename.

    Rejects any name containing `/`, `\\`, or `..`, any name starting with
    `.` (dotfiles), the empty string, and any name not ending in `.music`
    or `.txt`.

    Args:
        name: Candidate filename.

    Returns:
        True iff `name` is safe to join under `appdata/webmusicdata/scores/` and
        names a `.music`/`.txt` file.
    """
    if not name:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if name.startswith("."):
        return False
    return _has_score_extension(name)


def normalize_score_name(name: str) -> str:
    """Trim whitespace and ensure a `.music`/`.txt` extension.

    Appends `.music` when `name` (after trimming) has neither extension.

    Args:
        name: Candidate filename, possibly missing an extension or padded
            with whitespace.

    Returns:
        The normalized, valid score filename.

    Raises:
        ValueError: If the normalized result is not `is_valid_score_name`
            (e.g. it is empty, traverses, or is a dotfile).
    """
    trimmed = name.strip()
    if not _has_score_extension(trimmed):
        trimmed = trimmed + ".music"
    if not is_valid_score_name(trimmed):
        raise ValueError(f"invalid score name: {name!r}")
    return trimmed


def score_rel_path(name: str) -> str:
    """Build the scores-directory-relative path for a score name.

    Args:
        name: Candidate score filename (normalized via
            `normalize_score_name` first).

    Returns:
        `appdata/webmusicdata/scores/<normalized name>`.

    Raises:
        ValueError: If `name` does not normalize to a valid score name —
            defense-in-depth before the caller's `resolve_in_root`.
    """
    normalized = normalize_score_name(name)
    return f"{_SCORES_DIR_REL}/{normalized}"


def filter_and_sort_scores(names_json: str) -> str:
    """Keep only valid score filenames, sorted case-insensitively.

    Args:
        names_json: JSON-encoded list of filenames (e.g. an `os.listdir`
            result), as strings.

    Returns:
        JSON-encoded list of the kept names (those passing
        `is_valid_score_name` — `.music`/`.txt` extension, no dotfiles),
        sorted case-insensitively.
    """
    try:
        names = json.loads(names_json)
    except (ValueError, TypeError):
        names = []
    if not isinstance(names, list):
        names = []
    kept = [n for n in names if isinstance(n, str) and is_valid_score_name(n)]
    kept.sort(key=str.lower)
    return json.dumps(kept)


def _coerce_non_negative_int(value: object) -> int:
    """Coerce a JSON-decoded value to a non-negative int, defaulting to 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def parse_session(text: str) -> Optional[dict]:
    """Parse `session.json` text into `{last_file, caret, scroll}`.

    Args:
        text: Raw JSON text read from `appdata/webmusicdata/session.json`.

    Returns:
        A dict with keys `last_file` (str), `caret` (non-negative int),
        and `scroll` (non-negative int) — or `None` if `text` is missing,
        corrupt, not an object, or names an invalid/traversing
        `last_file`. `caret`/`scroll` default to 0 when absent, negative,
        or not an int. Never raises.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    last_file = data.get("last_file")
    if not isinstance(last_file, str) or not is_valid_score_name(last_file):
        return None
    return {
        "last_file": last_file,
        "caret": _coerce_non_negative_int(data.get("caret")),
        "scroll": _coerce_non_negative_int(data.get("scroll")),
    }


def build_session(current: str, caret: int = 0, scroll: int = 0) -> dict:
    """Build the `session.json` payload for the current score.

    Args:
        current: The current score's relative filename.
        caret: Caret character offset to persist.
        scroll: Scroll-top pixel offset to persist.

    Returns:
        `{"version": 1, "last_file": current, "caret": caret, "scroll": scroll}`.
    """
    return {"version": 1, "last_file": current, "caret": caret, "scroll": scroll}
