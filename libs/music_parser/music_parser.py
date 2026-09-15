import re
import numpy as np
from dataclasses import dataclass, field

from libs.pitch import letter_octave_accidental_to_midi
from libs.tempo_map import build_tempo_map, beats_to_seconds, segment_seconds, TempoConflictError

_ORDER     = {'c': 0, 'd': 1, 'e': 2, 'f': 3, 'g': 4, 'a': 5, 'b': 6}

INSTRUMENTS = {
    'piano':           (0,  0),   # Acoustic Grand Piano
    'guitar':          (0, 25),   # Acoustic Guitar (steel)
    'guitar-electric': (0, 27),   # Electric Guitar (clean)
    'sitar':           (0, 104),  # Sitar
    'violin':          (0, 40),   # Violin
    'organ-reed':      (0, 20),   # Reed Organ
}

_NOTE_RE    = re.compile(r'^([a-gA-G])(\d|[\^vV]+)?([#b]?)(-*)$')
_VELO_RE    = re.compile(r'^[vV](\d+)$')
_TIMESIG_RE = re.compile(r'^(\d+)/(\d+)$')
_RESTM_RE   = re.compile(r'^_(\d+)$')
_BPM_RE     = re.compile(r'^[bB][pP][mM]=(\d+(?:\.\d+)?)$')
_REST_RE    = re.compile(r'^\.(\.*|-*|\d+)$')


@dataclass
class NoteEvent:
    time: float       # seconds from start of section
    duration: float   # seconds
    midi_note: int    # 0–127
    velocity: int     # 0–127
    channel: int      # MIDI channel
    line_idx: int     # 0-indexed line in source text
    col_start: int    # 0-indexed column of token start
    col_end: int      # 0-indexed column of token end (exclusive)
    beat_time: float = 0.0       # position in quarter-note beats within the section
    beat_duration: float = 0.0   # length in quarter-note beats


@dataclass
class ParseError:
    message: str
    line_idx: int      # 0-indexed line in source text
    col_start: int
    col_end: int
    section_name: str = ""


@dataclass
class Section:
    name: str
    line_start: int = 0        # 0-indexed line of section header (or first content line)
    beat_count: float = 0.0
    events: list = field(default_factory=list)   # list[NoteEvent]
    errors: list = field(default_factory=list)   # list[ParseError]
    bpm_changes: list = field(default_factory=list)   # list[tuple[beat, bpm]] in this section's beats


@dataclass
class ParsedScore:
    sections: dict = field(default_factory=dict)   # str -> Section, insertion order
    programs: dict = field(default_factory=dict)   # channel -> (bank, prog)
    errors:   list = field(default_factory=list)   # list[ParseError] (global / cross-section)
    instrument_channels: dict = field(default_factory=dict)  # lowercase name -> channel


def _midi(letter: str, octave: int, acc: str) -> int:
    return letter_octave_accidental_to_midi(letter, octave, acc)


def _default_oct(letter: str, anch_letter: str, anch_oct: int) -> int:
    return anch_oct if _ORDER[letter] >= _ORDER[anch_letter] else anch_oct + 1


def _note_octave(oct_field: str, letter: str, anch_let: str, anch_oct: int) -> int:
    """Octave for a note: default-octave rule, an explicit digit, or markers.

    An empty field uses the letter-vs-anchor default rule. A digit overrides directly.
    A run of `^`/`v`/`V` shifts the note's default octave: up one per `^`, down one
    per `v`/`V`.
    """
    if not oct_field:
        return _default_oct(letter, anch_let, anch_oct)
    if oct_field.isdigit():
        return int(oct_field)
    shift = oct_field.count('^') - oct_field.count('v') - oct_field.count('V')
    return _default_oct(letter, anch_let, anch_oct) + shift


def _dot_rest_increments(suffix: str) -> int:
    """Increments for a `.`-family rest: `.N` is N, `.`+dots/dashes is 1 + count."""
    return int(suffix) if suffix.isdigit() else 1 + len(suffix)


# ── Step 2: first-pass splitter ───────────────────────────────────────────────

@dataclass
class _RawSection:
    name: str                   # "" for unnamed topmost section
    header_line: int            # 0-indexed line of the name token (or first content line)
    lines: list                 # list of (raw_str, line_idx)


def _classify_line(raw: str) -> str:
    """Return 'blank', 'header', 'instrument', 'include', or 'tempo'.

    A section header is a single bare token (no spaces, no colon). A colon-less
    line with more than one token is a percussion instrument line (e.g.
    `CowBell 8/8 zzzz`); the percussion-vs-error decision is made later when the
    instrument name is resolved against the designed instruments.
    """
    stripped = raw.strip()
    if not stripped or stripped.startswith('#'):
        return 'blank'
    tokens = stripped.split()
    first = tokens[0]
    if first.lower() == 'include':
        return 'include'
    if ':' in first:
        return 'instrument'
    if _BPM_RE.match(first):
        return 'tempo'
    if len(tokens) > 1:
        return 'instrument'
    return 'header'


def _split_sections(text: str) -> list:
    """Split source text into RawSection objects."""
    raw_sections = []
    current = _RawSection(name="", header_line=0, lines=[])

    for line_idx, raw in enumerate(text.splitlines()):
        kind = _classify_line(raw)
        if kind == 'blank':
            continue
        if kind == 'header':
            if current.lines:
                raw_sections.append(current)
            name = raw.strip().split()[0]
            current = _RawSection(name=name, header_line=line_idx, lines=[])
        else:
            if not current.lines:
                current.header_line = line_idx   # unnamed section: first content line
            current.lines.append((raw, line_idx))

    if current.lines:
        raw_sections.append(current)

    return raw_sections


# ── Step 3 / 4: instrument-line parser ───────────────────────────────────────

_DEFAULT_DRUM_NOTE = 38   # Acoustic Snare — fallback when a percussion row has no drum picked


def _normalized_instrument_spec(value) -> dict:
    """Normalize an instruments-map value into a canonical spec dict.

    Accepts either a legacy ``(bank, program)`` 2-tuple (always melodic) or a dict
    carrying ``bank``/``program`` plus optional ``is_percussion``/``drum_note``.
    Returns ``{"bank", "program", "is_percussion", "drum_note"}``.
    """
    if isinstance(value, dict):
        return {
            "bank": value["bank"],
            "program": value["program"],
            "is_percussion": value.get("is_percussion", False),
            "drum_note": value.get("drum_note"),
        }
    bank, program = value
    return {"bank": bank, "program": program, "is_percussion": False, "drum_note": None}


def _resolve_instrument(name_lower: str, designed_instruments: dict | None) -> dict | None:
    """Return a canonical instrument spec for name_lower, or None if unknown.

    Checks designed instruments first (which may carry percussion info), then falls
    back to the built-in INSTRUMENTS dict. Both forms are normalized via
    _normalized_instrument_spec so callers always see the same dict shape.
    """
    if designed_instruments is not None and name_lower in designed_instruments:
        return _normalized_instrument_spec(designed_instruments[name_lower])
    if name_lower in INSTRUMENTS:
        return _normalized_instrument_spec(INSTRUMENTS[name_lower])
    return None


def _parse_percussion_run(token: str) -> list | None:
    """Decode an all-placeholder percussion run into hits and rests.

    A percussion run is a whitespace-delimited token composed solely of the
    characters ``z``/``Z`` (a drum hit), ``.`` (a rest), and ``-`` (a continuation
    that adds one increment to the preceding hit or rest). Whitespace between such
    runs is optional, so `zzzz` is four hits and `z...z--.` is hit, rest(3),
    hit(3), rest(1).

    Returns a list of ``(kind, increments, start, end)`` tuples where ``kind`` is
    ``'hit'`` or ``'rest'``, ``increments`` is the length in time-signature
    increments, and ``start``/``end`` are character offsets within the token.
    Returns None if the token is not a valid all-placeholder run (so the caller can
    fall through to normal token handling / the unrecognized-token error).
    """
    if not token or any(c not in 'zZ.-' for c in token):
        return None
    items = []
    i, n = 0, len(token)
    while i < n:
        start = i
        if token[i] in 'zZ':
            i += 1
            while i < n and token[i] == '-':
                i += 1
            items.append(('hit', i - start, start, i))
        elif token[i] == '.':
            i += 1
            while i < n and token[i] in '.-':
                i += 1
            items.append(('rest', i - start, start, i))
        else:   # a '-' with no preceding hit or rest: malformed run
            return None
    return items


def _parse_instrument_line(raw: str, line_idx: int, section_name: str,
                            instr_to_channel: dict, programs: dict,
                            errors: list,
                            designed_instruments: dict | None = None) -> tuple:
    """Parse one instrument line; return (list[NoteEvent], list[(beat, bpm)], track_beats)
    where track_beats is the total track length in quarter-note beats including trailing rests.

    Resolves instrument names by checking designed_instruments first (if provided),
    then falling back to the built-in INSTRUMENTS dict.
    """
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r'\S+', raw)]
    if not tokens:
        return [], [], 0.0

    def err(msg, cs=0, ce=0):
        errors.append(ParseError(msg, line_idx, cs, ce, section_name))

    first, fcs, fce = tokens[0]
    if ':' in first:
        instr, anchor_str = first.split(':', 1)
    else:
        instr, anchor_str = first, None
    instr = instr.lower()

    spec = _resolve_instrument(instr, designed_instruments)
    if spec is None:
        err(f"unknown instrument {instr!r}", fcs, fce)
        return [], [], 0.0

    is_percussion = spec["is_percussion"]
    drum_note = spec["drum_note"] if spec["drum_note"] is not None else _DEFAULT_DRUM_NOTE

    if is_percussion:
        anch_let, anch_oct = None, None
    elif anchor_str is None:
        err(f"melodic instrument {instr!r} needs an anchor (name:note)", fcs, fce)
        return [], [], 0.0
    else:
        am = re.match(r'^([a-gA-G])(\d)$', anchor_str)
        if not am:
            err(f"bad anchor {anchor_str!r}", fcs, fce)
            return [], [], 0.0
        anch_let = am.group(1).lower()
        anch_oct = int(am.group(2))

    bank_program = (spec["bank"], spec["program"])

    # Channel assignment: lowest unused channel from the full 0–15 pool,
    # shared across lines with the same instrument name.
    if instr not in instr_to_channel:
        used = set(instr_to_channel.values())
        ch = next((c for c in range(16) if c not in used), None)
        if ch is None:
            err(f"more than 16 instruments — skipping {instr!r}", fcs, fce)
            return [], [], 0.0
        instr_to_channel[instr] = ch
        programs[ch] = bank_program
    ch = instr_to_channel[instr]

    # Defaults: v80 4/4
    velocity       = round(80 * 127 / 100)
    beats_per_meas = 4
    inc_beats      = 1.0        # 4/4 → 1 increment = 1 quarter note
    t              = 0.0        # position in quarter-note beats
    beats_in_meas  = 0.0
    first_bar_seen = False
    events = []
    bpm_changes = []

    for tok, cs, ce in tokens[1:]:

        vm = _VELO_RE.match(tok)
        if vm:
            n = int(vm.group(1))
            if n < 1 or n > 100:
                err(
                    f"velocity {n} out of range 1–100"
                    + (" (v0 disallowed: MIDI note-on with velocity 0 is treated as"
                       " note-off by some synths; use v1 for near-silence)" if n == 0 else ""),
                    cs, ce,
                )
                continue
            velocity = round(n * 127 / 100)
            continue

        tm = _TIMESIG_RE.match(tok)
        if tm:
            beats_per_meas = int(tm.group(1))
            inc_beats      = 4.0 / int(tm.group(2))
            beats_in_meas  = 0.0
            continue

        bm = _BPM_RE.match(tok)
        if bm:
            bpm_val = float(bm.group(1))
            if bpm_val <= 0:
                err("bpm must be positive", cs, ce)
            else:
                bpm_changes.append((t, bpm_val))
            continue

        if tok == '|':
            if first_bar_seen:
                if abs(beats_in_meas - beats_per_meas) > 1e-9:
                    err(
                        f"measure has {beats_in_meas} beats, expected {beats_per_meas}",
                        cs, ce,
                    )
            first_bar_seen = True
            beats_in_meas  = 0.0
            continue

        if tok == '_':
            remaining = beats_per_meas - beats_in_meas
            if remaining > 1e-9:
                t += remaining * inc_beats
                beats_in_meas = float(beats_per_meas)
            continue

        rm = _RESTM_RE.match(tok)
        if rm:
            n_meas = int(rm.group(1))
            t += n_meas * beats_per_meas * inc_beats
            beats_in_meas = 0.0
            continue

        rest_match = _REST_RE.match(tok)
        if rest_match:
            increments = _dot_rest_increments(rest_match.group(1))
            t += increments * inc_beats
            beats_in_meas += increments
            continue

        if is_percussion:
            run = _parse_percussion_run(tok)
            if run is not None:
                for kind, increments, sub_start, sub_end in run:
                    if kind == 'hit':
                        events.append(NoteEvent(
                            time=0.0, duration=0.0,
                            beat_time=t, beat_duration=increments * inc_beats,
                            midi_note=drum_note,
                            velocity=velocity, channel=ch,
                            line_idx=line_idx,
                            col_start=cs + sub_start, col_end=cs + sub_end,
                        ))
                    t += increments * inc_beats
                    beats_in_meas += increments
                continue
        else:
            nm = _NOTE_RE.match(tok)
            if nm:
                letter = nm.group(1).lower()
                oct_field = nm.group(2) or ''
                acc    = nm.group(3)
                ndash  = len(nm.group(4))

                octave = _note_octave(oct_field, letter, anch_let, anch_oct)
                dur_beats = (1 + ndash) * inc_beats

                events.append(NoteEvent(
                    time=0.0, duration=0.0,
                    beat_time=t, beat_duration=dur_beats,
                    midi_note=_midi(letter, octave, acc),
                    velocity=velocity, channel=ch,
                    line_idx=line_idx, col_start=cs, col_end=ce,
                ))
                t += dur_beats
                beats_in_meas += 1 + ndash
                continue

        err(f"unrecognized token {tok!r}", cs, ce)

    return events, bpm_changes, t


# ── Step 5: include-line syntactic parse ──────────────────────────────────────

@dataclass
class _IncSectionRef:
    name: str
    col_start: int
    col_end: int

@dataclass
class _IncSilence:
    measures: int

@dataclass
class _IncBar:
    col_start: int
    col_end: int

@dataclass
class _IncVelo:
    fraction: float   # N/100

@dataclass
class _IncTimeSig:
    beats_per_meas: int
    inc_beats: float

@dataclass
class _IncBpm:
    bpm: float

@dataclass
class _IncDotRest:
    increments: int

@dataclass
class _IncRestToBar:
    pass


def _parse_include_line(raw: str, line_idx: int, section_name: str,
                        errors: list) -> list:
    """Parse one include line; return list of _Inc* items."""
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r'\S+', raw)]
    if not tokens:
        return []

    def err(msg, cs=0, ce=0):
        errors.append(ParseError(msg, line_idx, cs, ce, section_name))

    # Defaults: v100 4/4
    items = []
    beats_per_meas = 4
    inc_beats      = 1.0

    # tokens[0] is 'include' — skip it
    for tok, cs, ce in tokens[1:]:

        vm = _VELO_RE.match(tok)
        if vm:
            n = int(vm.group(1))
            items.append(_IncVelo(fraction=n / 100.0))
            continue

        tm = _TIMESIG_RE.match(tok)
        if tm:
            beats_per_meas = int(tm.group(1))
            inc_beats      = 4.0 / int(tm.group(2))
            items.append(_IncTimeSig(beats_per_meas=beats_per_meas, inc_beats=inc_beats))
            continue

        bm = _BPM_RE.match(tok)
        if bm:
            bpm_val = float(bm.group(1))
            if bpm_val <= 0:
                err("bpm must be positive", cs, ce)
            else:
                items.append(_IncBpm(bpm=bpm_val))
            continue

        rm = _RESTM_RE.match(tok)
        if rm:
            items.append(_IncSilence(measures=int(rm.group(1))))
            continue

        if tok == '|':
            items.append(_IncBar(col_start=cs, col_end=ce))
            continue

        if tok == '_':
            items.append(_IncRestToBar())
            continue

        rest_match = _REST_RE.match(tok)
        if rest_match:
            items.append(_IncDotRest(increments=_dot_rest_increments(rest_match.group(1))))
            continue

        # bare word — section reference (validated later)
        if re.match(r'^[A-Za-z_][\w.\-]*$', tok):
            items.append(_IncSectionRef(name=tok, col_start=cs, col_end=ce))
            continue

        err(f"unrecognized include token {tok!r}", cs, ce)

    return items


# ── Step 6: cycle detection + topological sort ────────────────────────────────

def _topo_sort(sections: dict, include_items: dict, errors_out: list) -> list:
    """Return section names in topological order. Cyclic sections get errors."""
    # include_items: section_name -> list of per-line _Inc* lists
    deps = {
        name: [it.name
               for line_items in lines
               for it in line_items
               if isinstance(it, _IncSectionRef)]
        for name, lines in include_items.items()
    }

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in sections}
    order = []
    cycle_reported = set()

    def dfs(name, stack):
        color[name] = GRAY
        stack = stack + [name]
        for dep in deps.get(name, []):
            if dep not in sections:
                continue   # unknown-section errors reported elsewhere
            if color[dep] == GRAY:
                if dep not in cycle_reported:
                    cycle_reported.add(dep)
                    # find the _IncSectionRef token that closes the cycle
                    for line_items in include_items.get(name, []):
                        found = False
                        for it in line_items:
                            if isinstance(it, _IncSectionRef) and it.name == dep:
                                errors_out.append(ParseError(
                                    f"include cycle: {' → '.join(stack + [dep])}",
                                    -1, it.col_start, it.col_end, name,
                                ))
                                found = True
                                break
                        if found:
                            break
            elif color[dep] == WHITE:
                dfs(dep, stack)
        color[name] = BLACK
        order.append(name)

    for name in sections:
        if color[name] == WHITE:
            dfs(name, [])

    return order


# ── Step 7: expand includes ───────────────────────────────────────────────────

def _expand_includes(section_name: str, items: list, sections: dict,
                     errors: list) -> tuple:
    """Return (events, end_beats, bpm_changes) for one include track."""
    t = 0.0              # current position in quarter-note beats
    velo_fraction = 1.0  # default v100
    beats_per_meas = 4
    inc_beats = 1.0
    beats_in_track = 0.0
    first_bar = True
    events = []
    bpm_changes = []

    for item in items:
        if isinstance(item, _IncVelo):
            velo_fraction = item.fraction
        elif isinstance(item, _IncTimeSig):
            beats_per_meas = item.beats_per_meas
            inc_beats = item.inc_beats
        elif isinstance(item, _IncSilence):
            t += item.measures * beats_per_meas * inc_beats
            beats_in_track += item.measures * beats_per_meas
        elif isinstance(item, _IncDotRest):
            t += item.increments * inc_beats
            beats_in_track += item.increments
        elif isinstance(item, _IncRestToBar):
            remaining = beats_per_meas - beats_in_track
            if remaining > 1e-9:
                t += remaining * inc_beats
                beats_in_track = float(beats_per_meas)
        elif isinstance(item, _IncBpm):
            bpm_changes.append((t, item.bpm))
        elif isinstance(item, _IncBar):
            if not first_bar:
                if abs(beats_in_track % beats_per_meas) > 1e-9 and beats_in_track > 0:
                    errors.append(ParseError(
                        f"include bar mismatch at beat {beats_in_track}",
                        -1, item.col_start, item.col_end, section_name,
                    ))
            first_bar = False
            beats_in_track = 0.0
        elif isinstance(item, _IncSectionRef):
            ref = sections.get(item.name)
            if ref is None:
                errors.append(ParseError(
                    f"unknown section {item.name!r}",
                    -1, item.col_start, item.col_end, section_name,
                ))
                continue
            if ref.errors:
                errors.append(ParseError(
                    f"section {item.name!r} has errors, cannot include",
                    -1, item.col_start, item.col_end, section_name,
                ))
                continue
            for ev in ref.events:
                raw_v_100 = ev.velocity * 100 / 127
                scaled_100 = raw_v_100 * velo_fraction
                if scaled_100 > 100:
                    errors.append(ParseError(
                        f"include v{int(velo_fraction*100)} scales a note above 100",
                        -1, item.col_start, item.col_end, section_name,
                    ))
                    continue
                new_vel = round(scaled_100 * 127 / 100)
                events.append(NoteEvent(
                    time=0.0,
                    duration=0.0,
                    beat_time=t + ev.beat_time,
                    beat_duration=ev.beat_duration,
                    midi_note=ev.midi_note,
                    velocity=new_vel,
                    channel=ev.channel,
                    line_idx=ev.line_idx,
                    col_start=ev.col_start,
                    col_end=ev.col_end,
                ))
            # Propagate included section's tempo changes offset into this timeline
            for cb, cbpm in ref.bpm_changes:
                bpm_changes.append((t + cb, cbpm))
            t += ref.beat_count
            beats_in_track += ref.beat_count

    return events, t, bpm_changes


# ── Standalone tempo-line parser ──────────────────────────────────────────────

def _parse_tempo_line(raw: str, line_idx: int, section_name: str,
                      errors: list) -> list:
    """Parse a standalone tempo line; return list[(beat, bpm)] at beat 0."""
    changes = []
    for m in re.finditer(r'\S+', raw):
        tok, cs, ce = m.group(), m.start(), m.end()
        bm = _BPM_RE.match(tok)
        if bm:
            bpm_val = float(bm.group(1))
            if bpm_val <= 0:
                errors.append(ParseError("bpm must be positive", line_idx, cs, ce, section_name))
            else:
                changes.append((0.0, bpm_val))
        else:
            errors.append(ParseError(f"unexpected token {tok!r} on tempo line",
                                     line_idx, cs, ce, section_name))
    return changes


# ── Main entry point ──────────────────────────────────────────────────────────

def parse_score(text: str, instruments: dict | None = None) -> ParsedScore:
    """Parse a score text into a ParsedScore.

    Args:
        text: The raw score source text.
        instruments: Optional mapping of lowercase instrument name → spec for
            user-designed instruments. A spec is either a legacy ``(bank, program)``
            2-tuple (melodic) or a dict ``{"bank", "program", "is_percussion",
            "drum_note"}``. A percussion spec makes that instrument's lines use the
            anchor-free ``z``-hit syntax, sounding ``drum_note`` on its channel.
            When resolving an instrument name, this dict is checked first; the
            built-in INSTRUMENTS dict is the fallback.
    """
    score = ParsedScore()
    instr_to_channel: dict = {}

    # Pass 1: split into raw sections
    raw_sections = _split_sections(text)
    if not raw_sections:
        return score

    # Pass 2: parse instrument lines and collect include items per section
    include_items: dict = {}   # section_name -> list of per-line _Inc* lists

    for rs in raw_sections:
        sec = Section(name=rs.name, line_start=rs.header_line)
        score.sections[rs.name] = sec
        inc_lines_for_sec = []

        for raw, line_idx in rs.lines:
            kind = _classify_line(raw)
            if kind == 'instrument':
                evs, line_bpm, track_beats = _parse_instrument_line(
                    raw, line_idx, rs.name,
                    instr_to_channel, score.programs, sec.errors,
                    designed_instruments=instruments,
                )
                if evs:
                    sec.beat_count = max(sec.beat_count, track_beats)
                sec.events.extend(evs)
                sec.bpm_changes.extend(line_bpm)
            elif kind == 'include':
                items = _parse_include_line(raw, line_idx, rs.name, sec.errors)
                inc_lines_for_sec.append(items)
            elif kind == 'tempo':
                sec.bpm_changes.extend(_parse_tempo_line(raw, line_idx, rs.name, sec.errors))

        include_items[rs.name] = inc_lines_for_sec

    # Pass 3: cycle detection + topological sort
    topo_order = _topo_sort(score.sections, include_items, score.errors)

    # Pass 4: expand includes in topological order; each include line is an
    # independent parallel track starting at beat 0.
    for name in topo_order:
        sec = score.sections[name]
        inc_lines = include_items.get(name, [])
        for items in inc_lines:
            if not items:
                continue
            inc_events, inc_end_beats, inc_bpm = _expand_includes(
                name, items, score.sections, sec.errors,
            )
            sec.events.extend(inc_events)
            sec.bpm_changes.extend(inc_bpm)
            sec.beat_count = max(sec.beat_count, inc_end_beats)

    # Final pass: build per-section tempo map and convert beat positions to seconds
    for sec in score.sections.values():
        try:
            tempo_map = build_tempo_map(sec.bpm_changes)
        except TempoConflictError as exc:
            sec.errors.append(ParseError(str(exc), -1, 0, 0, sec.name))
            continue
        for ev in sec.events:
            ev.time = beats_to_seconds(ev.beat_time, tempo_map)
            ev.duration = segment_seconds(
                ev.beat_time, ev.beat_time + ev.beat_duration, tempo_map)
        sec.events.sort(key=lambda e: e.beat_time)

    score.instrument_channels = instr_to_channel
    return score


def _first_or_named_section(score: ParsedScore, section: str) -> Section:
    """Return the named section, or the first (topmost) section when section is empty."""
    return score.sections[section] if section else next(iter(score.sections.values()))


def percussion_instrument_map(name: str, bank: int, program: int, drum_note: int) -> dict:
    """Build a one-entry designed-instruments map for a percussion instrument.

    Plan-file args cannot express dict literals, so tests use this to construct the
    ``instruments`` mapping passed to parse_score / event_grid / section_beat_count.
    """
    return {name.lower(): {"bank": bank, "program": program,
                           "is_percussion": True, "drum_note": drum_note}}


def event_grid(text: str, section: str = "", instruments: dict | None = None) -> "np.ndarray":
    """Return an (N,4) array of [beat_time, beat_duration, midi_note, velocity] for one
    section's note events, sorted by beat_time.

    section="" selects the first (topmost) section. Empty selection yields shape (0,4).
    The ``instruments`` map accepts the same spec forms as parse_score (legacy
    ``(bank, program)`` tuples or percussion-aware dicts).
    """
    sec = _first_or_named_section(parse_score(text, instruments=instruments), section)
    rows = [[e.beat_time, e.beat_duration, e.midi_note, e.velocity]
            for e in sorted(sec.events, key=lambda e: e.beat_time)]
    return np.array(rows, dtype=float).reshape(-1, 4)


def section_beat_count(text: str, section: str = "", instruments: dict | None = None) -> float:
    """Return the beat_count (max track duration in quarter-note beats) of one section.

    section="" selects the first (topmost) section.
    """
    return _first_or_named_section(parse_score(text, instruments=instruments), section).beat_count
