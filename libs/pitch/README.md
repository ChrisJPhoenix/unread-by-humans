# pitch

Parses pitch designators like `C4`, `D#3`, `Bb5` and chord strings into MIDI
note numbers (0–127). The library is shared by `libs/music_parser` (which calls
it during score parsing) and `music/instruments_pane.py` (which uses it to
validate note input in the instrument configuration UI). Empty input or any
unrecognised designator raises `PitchParseError`.

## Public API

| Symbol | Description |
|---|---|
| `letter_octave_accidental_to_midi(letter, octave, acc) -> int` | Convert a letter (`a`–`g`, case-insensitive), integer octave, and accidental (`''`, `'#'`, `'b'`) to a MIDI note number. Raises `PitchParseError` if the result is outside 0–127 or any input is invalid. |
| `note_designator_to_midi(designator) -> int` | Parse a single designator string such as `"C4"` or `"D#3"` and return its MIDI note number. Raises `PitchParseError` on empty or malformed input. |
| `parse_pitch_chord(text) -> list[int]` | Split a chord string on any combination of comma, whitespace, or hyphen separators and return a list of MIDI note numbers. Raises `PitchParseError` on empty or malformed input. |
| `PitchParseError` | `ValueError` subclass raised for any unparseable pitch input. |

**Chord separators:** `parse_pitch_chord` treats any run of commas, spaces,
and hyphens as a single separator, so `"C4 E4 G4"`, `"C4,E4,G4"`, and
`"C4-E4-G4"` are all equivalent.

## Test plans

| File | Purpose |
|---|---|
| `test_primary.txt` | Primary plan covering the full public API — letter/octave/accidental conversion, single designators, chord separators, and error cases. |
