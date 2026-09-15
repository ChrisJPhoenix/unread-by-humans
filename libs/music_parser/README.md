# music_parser

Parses the music text format (specified in `design/text_format.md`) into a `ParsedScore`
data structure. The library handles multi-section scores with named instruments, velocity
and time-signature tokens, rest notation, BPM changes, and cross-section `include`
directives (with cycle detection and topological ordering). It delegates beat-to-seconds
conversion to `libs/tempo_map`.

## Public API

| Symbol | Description |
|---|---|
| `parse_score(text, instruments=None) -> ParsedScore` | Parse a complete score text. `instruments` is an optional `dict[str, spec]` that overrides or extends the built-in `INSTRUMENTS` dict. A `spec` is either a legacy `(bank, program)` 2-tuple (melodic) or a dict `{"bank", "program", "is_percussion", "drum_note"}`; a percussion spec makes that instrument's lines use the anchor-free `z`-hit syntax. |
| `event_grid(text, section="", instruments=None) -> np.ndarray` | Convenience wrapper: returns an `(N, 4)` float array of `[beat_time, beat_duration, midi_note, velocity]` rows sorted by `beat_time`. `section=""` selects the first section. |
| `section_beat_count(text, section="", instruments=None) -> float` | Convenience wrapper: returns `Section.beat_count` (max track duration in quarter-note beats) for one section. `section=""` selects the first section. |
| `percussion_instrument_map(name, bank, program, drum_note) -> dict` | Build a one-entry percussion `instruments` map. Exists because plan-file args cannot express dict literals; tests call it then pass the result to `event_grid`/`parse_score`. |
| `NoteEvent` | Dataclass: `beat_time`, `beat_duration`, `midi_note`, `velocity`, `channel`, `time`, `duration`, `line_idx`, `col_start`, `col_end`. |
| `Section` | Dataclass: `name`, `line_start`, `beat_count`, `events: list[NoteEvent]`, `errors: list[ParseError]`, `bpm_changes`. |
| `ParsedScore` | Dataclass: `sections: dict[str, Section]` (insertion-ordered), `programs: dict[int, (bank, prog)]`, `errors: list[ParseError]`, `instrument_channels: dict[str, int]`. |
| `ParseError` | Dataclass: `message`, `line_idx`, `col_start`, `col_end`, `section_name`. |
| `INSTRUMENTS` | Built-in `dict[str, (bank, program)]` mapping lowercase instrument names to MIDI bank/program pairs. |

## Data shapes

- `ParsedScore.sections` is an insertion-ordered `dict` mapping section name → `Section`. The unnamed top-level section uses the empty string `""` as its key.
- `Section.events` is a flat `list[NoteEvent]`, sorted by `beat_time` after parsing.
- `Section.beat_count` is the max track duration in quarter-note beats (the end of the last note on the longest track).
- `ParseError` carries `section_name` / `line_idx` / `col_start` / `col_end` / `message` for precise error reporting.

## Inspection helpers

`event_grid` and `section_beat_count` provide a small, plan-testable surface over
`parse_score` that is convenient both for test plans and for host code that only needs
the beat-level note grid without touching the full `ParsedScore` dataclass hierarchy.

## Links

- `design/text_format.md` — the text-format spec this library implements.
- `design/music_app.md` — overall data-flow architecture.

## Test plans

| File | Purpose |
|---|---|
| `test_primary.txt` | Primary plan covering `event_grid` and `section_beat_count`, including the core percussion `z`-hit cases — all stanzas run in both `--quick` and `--full` modes. |
| `test_percussion.txt` | Percussion edge cases (rests after hits, dash-vs-dot binding, velocity, default 4/4, colon-less-melodic error) — runs in `--full`. |
