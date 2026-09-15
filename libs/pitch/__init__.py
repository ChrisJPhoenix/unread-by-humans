"""pitch — parse note designators / chord strings to MIDI note numbers."""
from libs.pitch.pitch import (
    PitchParseError,
    letter_octave_accidental_to_midi,
    note_designator_to_midi,
    parse_pitch_chord,
)

__all__ = [
    "PitchParseError",
    "letter_octave_accidental_to_midi",
    "note_designator_to_midi",
    "parse_pitch_chord",
]
