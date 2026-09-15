# music_scores

Score-filename validation and session-persistence helpers for the `/music`
web port's file layer (`design/music-web-files.md`). Pure string/list/int
logic — no Flask, no filesystem, no imports from `music/`. All public
functions are fully implemented; no stubs.

Structured (list/dict) boundary values travel as JSON strings, matching the
`libs/music_web_instruments` convention, so every function is directly
callable from the plan-file test harness (which has no list/dict literal
syntax).

---

## API

```python
from libs import music_scores

# Traversal/extension guard for a bare filename.
ok = music_scores.is_valid_score_name(name)

# Trim + default extension; raises ValueError if still invalid.
normalized = music_scores.normalize_score_name(name)

# appdata/webmusicdata/scores/<normalized name>; raises ValueError on an invalid name.
rel_path = music_scores.score_rel_path(name)

# Keep only valid score names, sorted case-insensitively (JSON list in/out).
kept_json = music_scores.filter_and_sort_scores(names_json)

# Parse session.json text into {last_file, caret, scroll}, or None.
session = music_scores.parse_session(text)

# Build the session.json payload for the current score.
session = music_scores.build_session(current, caret=0, scroll=0)
```

### `is_valid_score_name(name: str) -> bool`

True iff `name` is a bare filename with no `/`, `\`, `..`, no leading dot,
ending in `.music` or `.txt`. Empty string is always `False`. The
traversal guard's first line of defense before `resolve_in_root`.

### `normalize_score_name(name: str) -> str`

Trims whitespace; appends `.music` if the trimmed name has neither
`.music` nor `.txt`. Raises `ValueError` if the normalized result is not
`is_valid_score_name`.

### `score_rel_path(name: str) -> str`

`appdata/webmusicdata/scores/<name>` after `normalize_score_name`. Raises
`ValueError` on an invalid name — defense-in-depth before the caller's
`resolve_in_root`.

### `filter_and_sort_scores(names_json: str) -> str`

Accepts a JSON-encoded list of filenames (e.g. an `os.listdir` result).
Returns a JSON-encoded list keeping only entries that pass
`is_valid_score_name` (drops dotfiles and non-`.music`/`.txt` entries),
sorted case-insensitively.

### `parse_session(text: str) -> dict | None`

Accepts raw `session.json` text. Returns `{"last_file": str, "caret":
int, "scroll": int}`, or `None` if the payload is missing, corrupt, not
an object, or names an invalid/traversing `last_file`. `caret`/`scroll`
are coerced to non-negative ints, defaulting to 0 when absent, negative,
or not an int. Never raises.

### `build_session(current: str, caret: int = 0, scroll: int = 0) -> dict`

Returns `{"version": 1, "last_file": current, "caret": caret, "scroll":
scroll}` — the payload the route glue writes to
`appdata/webmusicdata/session.json`.

---

## Test plans

| File | Mode | What it covers |
|------|------|----------------|
| `test_primary.txt` | quick + full | Valid/invalid names (traversal, backslash, dotfile, extension-less, empty); `normalize_score_name` round-trips + raise-on-invalid; `score_rel_path` happy path + raise-on-invalid; `filter_and_sort_scores` mixed-extension/dotfile/case list; `parse_session` good/missing/corrupt/traversing payloads and caret/scroll coercion + defaulting. |
