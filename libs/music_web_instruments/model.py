"""music_web_instruments — MIDI channel/instrument/automation helpers for the web port.

All public functions accept JSON-string boundary parameters so they flow
cleanly across process and HTTP boundaries.  No tkinter, no FluidSynth, and no
imports from music/.
"""
from __future__ import annotations

import json
import math


# ── Exponential slider math (ported from music/exponential_slider.py) ─────────
# Duplicated here so libs/music_web_instruments has no dependency on music/.

def _slider_position_for_exponential(value: float, lo: float, hi: float) -> float:
    """Map a physical value to a normalized slider position in [0, 1].

    Args:
        value: Physical value to convert.
        lo: Physical value at the left extreme (position 0).
        hi: Physical value at the right extreme (position 1).

    Returns:
        log(value/lo) / log(hi/lo), or 0.0 for non-positive inputs.
    """
    if value <= 0 or lo <= 0 or hi <= 0:
        return 0.0
    return math.log(value / lo) / math.log(hi / lo)


def _slider_value_to_exponential(slider_pos: float, lo: float, hi: float) -> float:
    """Map a normalized slider position in [0, 1] to a physical value.

    Args:
        slider_pos: Normalized position, 0.0 = left, 1.0 = right.
        lo: Physical value at position 0.
        hi: Physical value at position 1.

    Returns:
        lo * (hi / lo) ** slider_pos
    """
    return lo * (hi / lo) ** slider_pos


# ── CC value sub-maps (ported from music/instruments_pane.py) ─────────────────

def _vibrato_rate_hz_to_cc(rate_hz: float) -> int:
    """Map vibrato rate (0.1..100 Hz) onto CC 76 in 0..127.

    The slider runs exponentially from 100 Hz (left, position 0) to 0.1 Hz
    (right, position 1).  Position 0 maps to CC 127 (fastest); position 1 maps
    to CC 0 (slowest) — higher CC drives faster vibrato in most SoundFonts.

    Args:
        rate_hz: Vibrato rate in Hz.

    Returns:
        Integer CC value in 0..127.
    """
    pos = _slider_position_for_exponential(rate_hz, 100.0, 0.1)
    return int(round((1.0 - pos) * 127))


def _seconds_exp_to_cc(value_s: float, lo: float, hi: float) -> int:
    """Map an exponential-slider seconds value onto a CC in 0..127.

    Args:
        value_s: The time value in seconds.
        lo: The physical minimum (slider position 0).
        hi: The physical maximum (slider position 1).

    Returns:
        Integer CC value in 0..127.
    """
    pos = _slider_position_for_exponential(value_s, lo, hi)
    pos = max(0.0, min(1.0, pos))
    return int(round(pos * 127))


# ── Row defaults (mirrors InstrumentRow._DEFAULTS) ────────────────────────────

_DEFAULT_VIBRATO_RATE_HZ = _slider_value_to_exponential(0.5, 100.0, 0.1)

_ROW_DEFAULTS: dict = {
    "bank": 0,
    "program": 0,
    "is_percussion": False,
    "drum_note": None,
    "volume": 100,
    "pan": 64,
    "reverb": 40,
    "chorus": 0,
    "vibrato_depth": 0,
    "vibrato_rate_hz": None,   # resolved lazily — see _resolve_row
    "vibrato_delay_s": 0.01,
    "attack_s": 0.01,
    "decay_s": 0.1,
    "sustain_pct": 100,
    "release_s": 0.1,
    "expr_repeat_s": 1.0,
    "brightness_repeat_s": 1.0,
    "bend_repeat_s": 1.0,
}

# Default curve dicts for the three automation channels.
_DEFAULT_EXPR_CURVE: dict = {
    "value_min": 0, "value_max": 127,
    "value_min_label": "0", "value_max_label": "127",
    "time_left_label": "", "time_right_label": "",
    "initial_left_y": 127, "initial_right_y": 127,
    "points": [[0.0, 127], [1.0, 127]],
}
_DEFAULT_BRIGHTNESS_CURVE: dict = {
    "value_min": 0, "value_max": 127,
    "value_min_label": "0", "value_max_label": "127",
    "time_left_label": "", "time_right_label": "",
    "initial_left_y": 64, "initial_right_y": 64,
    "points": [[0.0, 64], [1.0, 64]],
}
_DEFAULT_BEND_CURVE: dict = {
    "value_min": -6, "value_max": 6,
    "value_min_label": "-6", "value_max_label": "+6",
    "time_left_label": "", "time_right_label": "",
    "initial_left_y": 0, "initial_right_y": 0,
    "points": [[0.0, 0], [1.0, 0]],
}


def _resolve_row(row: dict) -> dict:
    """Merge a row dict with defaults, returning a fully-populated row.

    Args:
        row: Partial row dict (may be missing any key from _ROW_DEFAULTS).

    Returns:
        Dict with every key populated (row values take priority over defaults).
    """
    resolved = {k: row.get(k, v) for k, v in _ROW_DEFAULTS.items()}
    if resolved["vibrato_rate_hz"] is None:
        resolved["vibrato_rate_hz"] = _DEFAULT_VIBRATO_RATE_HZ
    return resolved


def _patch_ccs_from_row(row: dict) -> dict[int, int]:
    """Compute the initial CC dict for a fully-resolved row dict.

    Args:
        row: Fully-resolved row dict (as from _resolve_row).

    Returns:
        Dict mapping MIDI CC number → initial value (0–127).
    """
    return {
        7:  int(row["volume"]),
        10: int(row["pan"]),
        91: int(row["reverb"]),
        93: int(row["chorus"]),
        1:  int(row["vibrato_depth"]),
        76: _vibrato_rate_hz_to_cc(float(row["vibrato_rate_hz"])),
        77: _seconds_exp_to_cc(float(row["vibrato_delay_s"]), 0.01, 10.0),
        73: _seconds_exp_to_cc(float(row["attack_s"]), 0.001, 2.0),
        72: _seconds_exp_to_cc(float(row["release_s"]), 0.001, 2.0),
    }


def _automation_list_from_row(row: dict) -> list[dict]:
    """Build the automation descriptor list from a fully-resolved row dict.

    Each entry carries a curve (as a serializable dict) and repeat_s.
    The caller (e.g. music_render) is responsible for reconstructing Curve
    objects from the dicts if needed.

    Args:
        row: Fully-resolved row dict.

    Returns:
        List of three automation dicts (expression CC11, brightness CC74, bend).
    """
    expr_curve = row.get("expr_curve", _DEFAULT_EXPR_CURVE)
    brightness_curve = row.get("brightness_curve", _DEFAULT_BRIGHTNESS_CURVE)
    bend_curve = row.get("bend_curve", _DEFAULT_BEND_CURVE)
    return [
        {
            "target": "cc",
            "cc": 11,
            "curve": expr_curve,
            "repeat_s": float(row.get("expr_repeat_s", 1.0)),
        },
        {
            "target": "cc",
            "cc": 74,
            "curve": brightness_curve,
            "repeat_s": float(row.get("brightness_repeat_s", 1.0)),
        },
        {
            "target": "bend",
            "curve": bend_curve,
            "repeat_s": float(row.get("bend_repeat_s", 1.0)),
            "bend_range": 6,
        },
    ]


def _designed_from_rows(rows: list[dict]) -> dict[str, dict]:
    """Build the name→spec designed-instruments map from a list of row dicts.

    Rows with an empty or missing name are skipped.  When two rows share a
    case-folded name, the last one wins (mirrors InstrumentsPane.designed_instruments).

    Args:
        rows: List of instrument-row dicts (each may be partial; defaults fill gaps).

    Returns:
        Dict mapping lowercase name → spec dict with keys:
        ``bank``, ``program``, ``is_percussion``, ``drum_note``, ``ccs``,
        ``automation``, ``pitch_bend_range``.
    """
    result: dict[str, dict] = {}
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        resolved = _resolve_row(row)
        result[name.lower()] = {
            "bank": int(resolved["bank"]),
            "program": int(resolved["program"]),
            "is_percussion": bool(resolved["is_percussion"]),
            "drum_note": resolved["drum_note"],
            "ccs": _patch_ccs_from_row(resolved),
            "automation": _automation_list_from_row(resolved),
            "pitch_bend_range": 6,
        }
    return result


# ── Public API ─────────────────────────────────────────────────────────────────

def build_channel_ccs(patch_json: str) -> dict:
    """Map a patch (JSON) to the initial MIDI CC values for its channel.

    Ports the ``build_channel_ccs`` function from ``music/instruments_pane.py``
    and the ``_vibrato_rate_hz_to_cc`` / ``_seconds_exp_to_cc`` sub-helpers.
    All exponential math is self-contained; no import from ``music/``.

    Args:
        patch_json: JSON-encoded patch dict containing at minimum:
            ``volume``, ``pan``, ``reverb``, ``chorus``, ``vibrato_depth``,
            ``vibrato_rate_hz``, ``vibrato_delay_s``, ``attack_s``, ``release_s``.
            Missing keys fall back to GM defaults.

    Returns:
        Dict mapping MIDI CC number (int) to its initial value (int, 0–127).
        Keys: 7 (volume), 10 (pan), 91 (reverb), 93 (chorus), 1 (vibrato depth),
        76 (vibrato rate), 77 (vibrato delay), 73 (attack), 72 (release).
    """
    patch = json.loads(patch_json)
    resolved = _resolve_row(patch)
    return _patch_ccs_from_row(resolved)


def instruments_arg_from_designed(rows_json: str) -> str:
    """Build the ``instruments`` argument (JSON) from designed instrument rows.

    Ports ``MusicApp._instruments_arg_from_designed`` from ``music/music.py``.
    Each row's name, bank, program, is_percussion, and drum_note are extracted;
    rows with an empty name are skipped.

    Args:
        rows_json: JSON-encoded list of instrument-row dicts.  Each dict may
            include any fields from the InstrumentRow state; missing fields
            fall back to GM defaults.

    Returns:
        JSON string encoding a dict mapping lowercase name → ``{"bank",
        "program", "is_percussion", "drum_note"}``.  This string can be
        decoded and passed directly as the ``instruments`` keyword argument to
        ``parse_score``.
    """
    rows = json.loads(rows_json)
    designed = _designed_from_rows(rows)
    instruments_arg = {
        name: {
            "bank": spec["bank"],
            "program": spec["program"],
            "is_percussion": spec["is_percussion"],
            "drum_note": spec["drum_note"],
        }
        for name, spec in designed.items()
    }
    return json.dumps(instruments_arg)


def channel_ccs_from_designed(rows_json: str, channels_json: str) -> dict:
    """Produce the per-channel initial CC dict keyed by channel number.

    Ports the CC-map half of ``MusicApp._channel_maps_from_score``.

    Args:
        rows_json: JSON-encoded list of instrument-row dicts (same schema as
            ``instruments_arg_from_designed``).
        channels_json: JSON-encoded ``dict[str, int]`` mapping lowercase
            instrument name → MIDI channel (as produced by
            ``parse_score(...).instrument_channels``).

    Returns:
        Dict mapping channel number (int) to its initial-CC sub-dict
        ``{cc_number: value}``.  Only channels whose instrument name appears
        in both ``rows`` and ``channels_json`` are included.
    """
    rows = json.loads(rows_json)
    instrument_channels: dict[str, int] = json.loads(channels_json)
    designed = _designed_from_rows(rows)
    return {
        ch: designed[name]["ccs"]
        for name, ch in instrument_channels.items()
        if name in designed
    }


def channel_automation_from_designed(rows_json: str, channels_json: str) -> dict:
    """Produce per-channel automation structures keyed by channel number.

    Ports the automation-map half of ``MusicApp._channel_maps_from_score`` and
    the ``InstrumentRow._build_automation_list`` helper.  Curves are stored as
    dicts (via ``slider_2d.to_dict`` format) so the result is JSON-serializable.

    Args:
        rows_json: JSON-encoded list of instrument-row dicts.
        channels_json: JSON-encoded ``dict[str, int]`` mapping lowercase name →
            MIDI channel.

    Returns:
        Dict mapping channel number (int) to a list of three automation dicts
        (expression CC11, brightness CC74, bend) keyed by channel.  Each entry
        has the form ``{"target": "cc"|"bend", "cc": int, "curve": dict,
        "repeat_s": float}`` (plus ``"bend_range": 6`` for bend entries).
    """
    rows = json.loads(rows_json)
    instrument_channels: dict[str, int] = json.loads(channels_json)
    designed = _designed_from_rows(rows)
    return {
        ch: designed[name]["automation"]
        for name, ch in instrument_channels.items()
        if name in designed
    }


def play_offset_for_caret(text: str, caret_offset: int) -> int:
    """Return the character offset of the note token that the caret is on.

    Implements the Play-button semantics from ``design/music_app.md``:
    "Plays the section containing the cursor, starting from the note event
    whose token the cursor is on (or the beginning if cursor is not on a note)."

    Scans all note events in the section that contains the caret (the last
    section whose header starts at or before the caret's line).  For each event
    the absolute character offset of its token is reconstructed from its
    ``line_idx`` and ``col_start``.  If the caret falls inside a note token,
    that token's absolute start offset is returned.  Otherwise 0 is returned
    (play from the start of the section).

    Args:
        text: The full score text.
        caret_offset: Character offset of the caret within ``text``.

    Returns:
        Absolute character offset of the note whose token contains the caret,
        or 0 if the caret is not on a note.
    """
    from libs.music_parser import parse_score

    lines = text.splitlines()
    # Map each line number to its absolute start offset in text.
    line_offsets = _build_line_offsets(lines)

    caret_line = _line_at_offset(line_offsets, caret_offset, len(text))

    score = parse_score(text)
    if not score.sections:
        return 0

    section = _section_at_caret_line(score, caret_line)
    if section is None:
        return 0

    for event in section.events:
        abs_start = line_offsets[event.line_idx] + event.col_start
        abs_end = line_offsets[event.line_idx] + event.col_end
        if abs_start <= caret_offset < abs_end:
            return abs_start

    return 0


def audition_score_and_programs(spec_json: str) -> str:
    """Build everything needed to render a one-instrument audition preview.

    spec_json is a JSON object = an instrument row dict (same fields as the rows
    in appdata/webmusicdata/instruments.json: bank, program, is_percussion, drum_note,
    volume, pan, reverb, chorus, vibrato_depth, vibrato_rate_hz, vibrato_delay_s,
    attack_s, decay_s, sustain_pct, release_s, expr_curve, brightness_curve,
    bend_curve, *_repeat_s) PLUS three audition keys:
        "pitch"      : str  — chord string, e.g. "C4 E4 G4" (ignored for percussion)
        "velocity"   : int  — default 100
        "duration_s" : float — default 1.0

    Returns a JSON string encoding:
        {
          "score":              [ {"events": [ {time, duration, midi_note,
                                                velocity, channel}, ... ]} ],
          "instruments":        {} (reserved; audition routes notes directly),
          "programs":           {"0": [bank, program]},
          "channel_ccs":        {"0": {cc_number: value, ...}},
          "channel_automation": {"0": [ automation-descriptor dicts ]}
        }
    Everything sounds on channel 0 (an audition preview is isolated).
    """
    from libs.pitch import parse_pitch_chord, PitchParseError

    spec = json.loads(spec_json)
    velocity = int(spec.get("velocity", 100))
    duration_s = float(spec.get("duration_s", 1.0))

    resolved = _resolve_row(spec)
    bank = int(resolved["bank"])
    program = int(resolved["program"])
    is_percussion = bool(resolved["is_percussion"])

    if is_percussion:
        drum_note = resolved["drum_note"]
        midi_note = int(drum_note) if drum_note is not None else 38
        events = [{"time": 0.0, "duration": duration_s, "midi_note": midi_note,
                   "velocity": velocity, "channel": 0}]
    else:
        pitch_text = spec.get("pitch", "")
        try:
            midi_notes = parse_pitch_chord(pitch_text)
        except PitchParseError as exc:
            raise ValueError(f"invalid pitch for audition: {exc}") from exc
        events = [
            {"time": 0.0, "duration": duration_s, "midi_note": note,
             "velocity": velocity, "channel": 0}
            for note in midi_notes
        ]

    ccs = _patch_ccs_from_row(resolved)
    automation = _automation_list_from_row(resolved)

    return json.dumps({
        "score": [{"events": events}],
        "instruments": {},
        "programs": {"0": [bank, program]},
        "channel_ccs": {"0": {str(k): v for k, v in ccs.items()}},
        "channel_automation": {"0": automation},
    })


def play_render_bundle(text: str, caret_offset: int, rows_json: str) -> str:
    """Build the full render bundle for the Play button, starting at the caret.

    Implements design/music_app.md Play semantics: play the section containing
    the caret, starting at the note whose token the caret is on (or the section
    start if the caret is not on a note).

    Steps:
      1. Build instruments_arg from rows_json (via instruments_arg_from_designed).
      2. Parse the score with those instruments.
      3. Find the section containing the caret (reusing line-offset + section helpers).
      4. Determine start_time: the .time of the event under the caret (from
         play_offset_for_caret); 0.0 if caret is not on a note.
      5. Emit that section's events with time >= start_time, each shifted by
         -start_time.

    Args:
        text: The full score text.
        caret_offset: Character offset of the caret within text.
        rows_json: JSON-encoded list of instrument-row dicts (same schema as
            instruments_arg_from_designed).

    Returns:
        JSON string with keys:
            "score"              — list of one section dict, {"events": [...]};
            "programs"           — {str(channel): [bank, program], ...};
            "channel_ccs"        — {str(channel): {cc: value, ...}, ...};
            "channel_automation" — {str(channel): [...descriptors...], ...}.
        Never raises; returns an empty bundle when there are no sections/events.
    """
    from libs.music_parser import parse_score

    instruments_arg = json.loads(instruments_arg_from_designed(rows_json))
    score = parse_score(text, instruments=instruments_arg or None)

    lines = text.splitlines()
    line_offsets = _build_line_offsets(lines)
    caret_line = _line_at_offset(line_offsets, caret_offset, len(text))
    section = _section_at_caret_line(score, caret_line) if score.sections else None

    # Collect events from the section that contains the caret.
    all_section_events = section.events if section is not None else []
    shifted_events: list[dict] = []
    if all_section_events:
        start_offset = play_offset_for_caret(text, caret_offset)
        start_time = _event_time_at_offset(section, line_offsets, start_offset)
        shifted_events = _shifted_events_from(all_section_events, start_time)

    channels_json = json.dumps(score.instrument_channels)
    programs = {str(ch): list(bp) for ch, bp in score.programs.items()}
    channel_ccs = {str(ch): ccs
                   for ch, ccs in channel_ccs_from_designed(rows_json, channels_json).items()}
    channel_automation = {str(ch): auto
                          for ch, auto in channel_automation_from_designed(
                              rows_json, channels_json).items()}

    return json.dumps({
        "score": [{"events": shifted_events}],
        "programs": programs,
        "channel_ccs": channel_ccs,
        "channel_automation": channel_automation,
    })


def _build_line_offsets(lines: list[str]) -> list[int]:
    """Return the absolute character start offset of each line.

    Args:
        lines: List of line strings (without line-ending characters).

    Returns:
        List where element i is the number of characters before line i in the
        original text (assuming newlines take exactly one character each).
    """
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1   # +1 for the newline character
    return offsets


def _line_at_offset(line_offsets: list[int], caret_offset: int,
                    text_length: int) -> int:
    """Return the 0-indexed line number that contains caret_offset.

    Args:
        line_offsets: Absolute start offsets per line (from _build_line_offsets).
        caret_offset: Absolute character offset of the caret.
        text_length: Total length of the text (to handle end-of-file position).

    Returns:
        0-indexed line number.
    """
    if not line_offsets:
        return 0
    line = 0
    for i, offset in enumerate(line_offsets):
        if offset <= caret_offset:
            line = i
        else:
            break
    return line


def _event_time_at_offset(section, line_offsets: list[int], token_offset: int) -> float:
    """Return the .time of the event whose token starts at token_offset.

    If token_offset is 0 (caret not on any note) or no event matches, returns 0.0.

    Args:
        section: A Section from parse_score (has .events list of NoteEvent).
        line_offsets: Absolute start offsets per line (from _build_line_offsets).
        token_offset: Absolute character offset of the note token (0 = not on a note).

    Returns:
        The float .time of the matching event, or 0.0 if not found.
    """
    if token_offset == 0:
        return 0.0
    for event in section.events:
        abs_start = line_offsets[event.line_idx] + event.col_start
        if abs_start == token_offset:
            return event.time
    return 0.0


def _shifted_events_from(events: list, start_time: float) -> list[dict]:
    """Return serializable event dicts for events at or after start_time, shifted.

    Each emitted event has its time reduced by start_time so the first event
    always appears at time 0.0 (or the smallest non-negative shifted time).

    Args:
        events: List of NoteEvent objects from a Section.
        start_time: The time to treat as the new origin; events before it are dropped.

    Returns:
        List of dicts with keys: time, duration, midi_note, velocity, channel.
    """
    return [
        {
            "time": round(ev.time - start_time, 9),
            "duration": ev.duration,
            "midi_note": ev.midi_note,
            "velocity": ev.velocity,
            "channel": ev.channel,
        }
        for ev in events
        if ev.time >= start_time - 1e-9
    ]


def _section_at_caret_line(score, caret_line: int):
    """Return the Section whose line range contains caret_line (0-indexed).

    The cursor belongs to the last section whose ``line_start`` is at or before
    ``caret_line``.  Falls back to the first section if none qualifies.

    Args:
        score: A ``ParsedScore`` returned by ``parse_score``.
        caret_line: 0-indexed line number of the caret.

    Returns:
        The Section containing the caret, or the first section as fallback,
        or None if there are no sections.
    """
    first = None
    best = None
    for sec in score.sections.values():
        if first is None:
            first = sec
        if sec.line_start <= caret_line:
            best = sec
        else:
            break
    # When the caret is before the first section's content (e.g. on a
    # section-header line), fall back to the first section so that clicking
    # anywhere in a section's header still plays that section.
    return best if best is not None else first
