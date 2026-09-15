"""Pitch-designator parser.

Shared between the parser (`music_parser._midi`) and the InstrumentsPane.
Accepts strings like "C4", "D#3", "Bb5". A chord string can list several
notes separated by any combination of commas, spaces, or hyphens. Returns
MIDI note numbers (0–127). Empty input or any illegal designator raises
`PitchParseError`.
"""
import re

_SEMITONES = {'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11}
_DESIGNATOR_RE = re.compile(r'^([A-Ga-g])([#b]?)(-?\d+)$')


class PitchParseError(ValueError):
    """Raised when a pitch designator or chord string can't be parsed."""


def letter_octave_accidental_to_midi(letter: str, octave: int, acc: str) -> int:
    """Return the MIDI note number for a letter+octave+accidental triple.

    `letter` is one of 'a'..'g' (lower- or upper-case). `acc` is '', '#', or 'b'.
    Raises `PitchParseError` if the result is outside 0..127 or inputs are invalid.
    """
    key = letter.lower()
    if key not in _SEMITONES:
        raise PitchParseError(f"illegal note letter: {letter!r}")
    semi = _SEMITONES[key]
    if acc == '#':
        semi += 1
    elif acc == 'b':
        semi -= 1
    elif acc != '':
        raise PitchParseError(f"illegal accidental: {acc!r}")
    midi = 12 * (octave + 1) + semi
    if midi < 0 or midi > 127:
        raise PitchParseError(f"note out of MIDI range: {letter}{acc}{octave}")
    return midi


def note_designator_to_midi(designator: str) -> int:
    """Return the MIDI note number for a single designator like "C4" or "D#3"."""
    if not isinstance(designator, str):
        raise PitchParseError(f"not a string: {designator!r}")
    s = designator.strip()
    if not s:
        raise PitchParseError("empty designator")
    m = _DESIGNATOR_RE.match(s)
    if not m:
        raise PitchParseError(f"illegal note designator: {designator!r}")
    return letter_octave_accidental_to_midi(m.group(1), int(m.group(3)), m.group(2))


def parse_pitch_chord(text: str) -> list[int]:
    """Parse a chord string into a list of MIDI note numbers.

    Separators may be any combination of comma, whitespace, or hyphen.
    Examples: "C4", "C4 E4 G4", "C4,E4,G4", "C4-E4-G4".
    Empty input or any illegal designator raises `PitchParseError`.
    """
    if not isinstance(text, str):
        raise PitchParseError(f"not a string: {text!r}")
    parts = [p for p in re.split(r'[,\s\-]+', text.strip()) if p]
    if not parts:
        raise PitchParseError("empty pitch input")
    return [note_designator_to_midi(p) for p in parts]
