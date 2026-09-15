# music_web_instruments

MIDI channel, instrument-row, and automation helpers for the music→web port.

All seven public functions are fully implemented; no stubs remain.

---

## API

```python
from libs import music_web_instruments

# Map a patch JSON to initial MIDI CC values for its channel.
ccs = music_web_instruments.build_channel_ccs(patch_json)

# Build the instruments argument string from designed rows.
arg = music_web_instruments.instruments_arg_from_designed(rows_json)

# Per-channel initial-CC dict from designed rows + channel assignments.
cc_map = music_web_instruments.channel_ccs_from_designed(rows_json, channels_json)

# Per-channel automation structures from designed rows + channel assignments.
auto_map = music_web_instruments.channel_automation_from_designed(rows_json, channels_json)

# Play-start sample/char offset for a caret position in the score text.
offset = music_web_instruments.play_offset_for_caret(text, caret_offset)

# Build score + programs + CCs + automation for a single-instrument audition preview.
result_json = music_web_instruments.audition_score_and_programs(spec_json)

# Build the full render bundle for the Play button, starting at the caret.
bundle_json = music_web_instruments.play_render_bundle(text, caret_offset, rows_json)
```

### `build_channel_ccs(patch_json: str) -> dict`

Accepts a JSON-encoded patch dict.  Returns a dict mapping MIDI CC number
(int) to initial value (int).  Ports `build_channel_ccs` from
`music/instruments_pane.py` including the vibrato-rate and seconds→CC sub-maps.

### `instruments_arg_from_designed(rows_json: str) -> str`

Accepts a JSON-encoded list of instrument-row dicts.  Returns a JSON string
encoding a `dict[str, dict]` suitable for passing to `parse_score(instruments=)`.
Ports `MusicApp._instruments_arg_from_designed` from `music/music.py`.

### `channel_ccs_from_designed(rows_json: str, channels_json: str) -> dict`

Accepts JSON-encoded instrument rows and channel assignments
(`parse_score().instrument_channels`, a `dict[str, int]`).  Returns a dict
mapping channel number (int) to an initial-CC sub-dict.  Ports the CC-map half
of `MusicApp._channel_maps_from_score`.

### `channel_automation_from_designed(rows_json: str, channels_json: str) -> dict`

Accepts JSON-encoded instrument rows and channel assignments.  Returns a dict
mapping channel number (int) to its automation-list (three entries: CC11,
CC74, bend).  Curves are stored as dicts (JSON-serializable).  Ports
`InstrumentRow._build_automation_list` and the automation half of
`_channel_maps_from_score`.

### `play_offset_for_caret(text: str, caret_offset: int) -> int`

Accepts the full score text and a caret character offset.  Returns the
absolute character offset of the note token under the caret (or 0 if the
caret is not on a note), implementing Play-from-cursor semantics from
`design/music_app.md`.

### `audition_score_and_programs(spec_json: str) -> str`

Accepts a JSON-encoded instrument-row dict (same schema as
`appdata/webmusicdata/instruments.json`) extended with `"pitch"` (chord string),
`"velocity"` (int, default 100), and `"duration_s"` (float, default 1.0).
Returns a JSON string with keys `"score"`, `"instruments"`, `"programs"`,
`"channel_ccs"`, and `"channel_automation"` — everything needed by
`render_song_pcm` to render a one-instrument audition preview on channel 0.
For melodic instruments the pitch string is parsed into MIDI note numbers via
`libs.pitch.parse_pitch_chord`; for percussion the `drum_note` value is used
directly (defaulting to 38 if null).  Raises `ValueError` for an invalid or
empty melodic pitch string.

### `play_render_bundle(text: str, caret_offset: int, rows_json: str) -> str`

Accepts the full score text, a caret character offset, and a JSON-encoded
list of instrument-row dicts.  Returns a JSON string with keys `"score"`,
`"programs"`, `"channel_ccs"`, and `"channel_automation"` — everything needed
by `render_song_pcm` to play the section containing the caret, starting at
the note token under the caret (or the section start if the caret is not on a
note).  Never raises; returns an empty bundle for an empty/unparseable score.

---

## Test plans

| File | Mode | What it covers |
|------|------|----------------|
| `test_primary.txt` | quick + full | All seven functions — default patches, known rows, exact string oracles for `instruments_arg_from_designed`, exact int oracles for `play_offset_for_caret`, contains oracles for `audition_score_and_programs` (melodic + chord + percussion + null drum_note + default velocity), contains oracles for `play_render_bundle` (caret-at-start, caret-on-later-note, empty text, two-section score). |
