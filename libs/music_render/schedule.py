"""music_render.schedule — render schedule construction and PCM rendering.

A render schedule is a list of timed events (note-on, note-off, CC, pitch-bend)
expressed in sample-accurate frame offsets.  The functions here build the
schedule from a parsed music structure, optionally fold automation into it,
and finally drive a backend to produce float32 PCM.

Score JSON format (input to build_render_schedule)
---------------------------------------------------
A JSON array of section dicts.  Each section dict has an ``events`` key
holding a list of note-event dicts, each with:
    ``time``      – seconds from the start of the section (float)
    ``duration``  – seconds (float)
    ``midi_note`` – MIDI note number 0–127 (int)
    ``velocity``  – 0–127 (int)
    ``channel``   – MIDI channel 0–15 (int)

Schedule JSON format (output / intermediate)
---------------------------------------------
A JSON array of event dicts, each with at least::

    {"type": <str>, "frame": <int>, ...type-specific fields...}

Known event types:

    note_on   – {"type": "note_on",  "frame": int, "note": int,
                 "velocity": int, "channel": int}
    note_off  – {"type": "note_off", "frame": int, "note": int,
                 "channel": int}
    cc        – {"type": "cc", "frame": int, "channel": int,
                 "cc": int, "value": int}
    bend      – {"type": "bend", "frame": int, "channel": int,
                 "value": int}

Events are sorted by ``frame`` ascending; at equal frames, note-off events
sort before note-on events so a re-triggered note restarts cleanly (mirroring
the ``_build_event_list`` sort order in ``music_player.py``).
"""
import json
from typing import Any

import numpy as np


# ── event-type sort priority (lower = earlier at the same frame) ──────────────
# note_off must precede note_on at equal frame so a retriggered note restarts.
_EVENT_SORT_KEY = {"note_off": 0, "cc": 1, "bend": 1, "note_on": 2}


def _event_frame_sort_key(event: dict) -> tuple[int, int]:
    """Return a sort key that puts note-offs before note-ons at the same frame."""
    return (event["frame"], _EVENT_SORT_KEY.get(event["type"], 1))


def _seconds_to_frame(seconds: float, sample_rate: int) -> int:
    """Convert a time in seconds to the nearest integer frame index."""
    return int(round(seconds * sample_rate))


def _note_on_event(frame: int, note: int, velocity: int, channel: int) -> dict:
    """Build a note-on schedule event dict."""
    return {"type": "note_on", "frame": frame,
            "note": note, "velocity": velocity, "channel": channel}


def _note_off_event(frame: int, note: int, channel: int) -> dict:
    """Build a note-off schedule event dict."""
    return {"type": "note_off", "frame": frame, "note": note, "channel": channel}


def build_render_schedule(score_json: str, sample_rate: int) -> str:
    """Build a sample-accurate render schedule from a parsed score.

    Converts every note event in the score into a pair of note-on and note-off
    schedule entries whose ``frame`` values are the nearest integer frame index
    for the event's wall-clock time (``round(time * sample_rate)``).  This
    mirrors the note-ordering logic in ``music_player._build_event_list``:
    note-offs sort before note-ons at the same frame so a re-triggered note
    restarts cleanly.

    Args:
        score_json: JSON array of section dicts.  Each section dict has an
            ``events`` key holding a list of note-event dicts with keys
            ``time`` (float, seconds), ``duration`` (float, seconds),
            ``midi_note`` (int), ``velocity`` (int), and ``channel`` (int).
        sample_rate: Audio sample rate in Hz (e.g. 44100).

    Returns:
        JSON string encoding a list of schedule-event dicts, sorted by frame
        (note-offs before note-ons at equal frames).
    """
    sections = json.loads(score_json)
    events: list[dict] = []
    for section in sections:
        for note_ev in section.get("events", []):
            on_frame  = _seconds_to_frame(float(note_ev["time"]),                          sample_rate)
            off_frame = _seconds_to_frame(float(note_ev["time"]) + float(note_ev["duration"]), sample_rate)
            note      = int(note_ev["midi_note"])
            velocity  = int(note_ev["velocity"])
            channel   = int(note_ev["channel"])
            events.append(_note_on_event(on_frame,  note, velocity, channel))
            events.append(_note_off_event(off_frame, note, channel))
    events.sort(key=_event_frame_sort_key)
    return json.dumps(events)


def fold_automation_into_schedule(schedule_json: str, automation_json: str) -> str:
    """Fold CC / pitch-bend automation points into a render schedule.

    Merges a list of pre-computed automation events (CC or pitch-bend sends at
    known frame offsets, produced by sampling an automation curve at chunk
    boundaries) into the base note-on/note-off schedule.  The merged result is
    sorted by frame; at equal frames, CC/bend events sort before note-on events
    (same priority as note-off — they settle channel state before any note
    triggers).

    Deduplication is the caller's responsibility: ``automation_json`` should
    already contain only the sends that pass the ``should_send`` gate from
    ``libs.slider_2d.automation_send``.

    Args:
        schedule_json: JSON string of the base schedule (as returned by
            ``build_render_schedule``).
        automation_json: JSON string encoding a list of automation-event dicts.
            Each dict must have at least ``{"type": "cc"|"bend", "frame": int,
            "channel": int, "value": int}``; CC events additionally carry
            ``"cc": int``.

    Returns:
        JSON string of the merged schedule, sorted by ``frame`` (note-offs and
        CC/bend events before note-ons at equal frames).
    """
    base_events   = json.loads(schedule_json)
    auto_events   = json.loads(automation_json)
    merged        = base_events + auto_events
    merged.sort(key=_event_frame_sort_key)
    return json.dumps(merged)


# ── PCM render ────────────────────────────────────────────────────────────────

_RENDER_CHUNK = 512  # frames per render iteration


def render_pcm(schedule_json: str, backend: Any, sample_rate: int) -> np.ndarray:
    """Drive ``backend`` with the render schedule to produce float32 PCM.

    Iterates through the schedule's events in frame order.  Between consecutive
    event frames the renderer pulls a chunk of audio from the backend.  Note-on
    and note-off events are dispatched with a ``sample_offset`` of 0 (they fire
    at the start of the chunk that begins at their target frame).  CC and bend
    events are dispatched to the backend only when the backend exposes ``cc``
    and ``pitch_bend`` methods; ``FakeBackend`` ignores them gracefully.

    Args:
        schedule_json: JSON string of the schedule (as returned by
            ``build_render_schedule`` / ``fold_automation_into_schedule``).
        backend: A synth backend satisfying the ``note_on`` / ``note_off`` /
            ``render_block`` interface (see ``libs.music_render.backend``).
        sample_rate: Audio sample rate in Hz.

    Returns:
        NumPy array of shape ``(total_frames, 2)``, dtype float32,
        containing interleaved stereo PCM.  ``total_frames`` equals the
        frame of the last note-off event plus a two-second silence tail.
    """
    events = json.loads(schedule_json)
    if not events:
        return np.zeros((0, 2), dtype=np.float32)

    tail_frames = 2 * sample_rate
    last_frame  = max(ev["frame"] for ev in events)
    total_frames = last_frame + tail_frames

    pieces: list[np.ndarray] = []
    rendered = 0

    def _pull_audio_until(target_frame: int) -> None:
        nonlocal rendered
        while rendered < target_frame:
            n = min(_RENDER_CHUNK, target_frame - rendered)
            pieces.append(backend.render_block(n))
            rendered += n

    for event in events:
        _pull_audio_until(event["frame"])
        _dispatch_event(backend, event)

    _pull_audio_until(total_frames)
    return np.concatenate(pieces, axis=0).astype(np.float32)


def render_pcm_with_fake_backend(schedule_json: str, sample_rate: int) -> np.ndarray:
    """Render a schedule to PCM using a fresh ``FakeBackend`` instance.

    Convenience wrapper for tests and pipelines that need a fully
    deterministic render without constructing a backend object in the call
    site.  Each call allocates an independent ``FakeBackend`` so renders
    are reproducible regardless of call order.

    Args:
        schedule_json: JSON string of the schedule (as returned by
            ``build_render_schedule`` / ``fold_automation_into_schedule``).
        sample_rate: Audio sample rate in Hz.

    Returns:
        NumPy array of shape ``(total_frames, 2)``, dtype float32.
    """
    from libs.music_render.backend import FakeBackend
    return render_pcm(schedule_json, FakeBackend(), sample_rate)


_DEFAULT_SAMPLE_RATE = 48000
_DEFAULT_SOUNDFONT = '/usr/share/sounds/sf2/MuseScore_General_Full.sf2'


def render_song_pcm(
    score_json: str,
    instruments_json: str,
    channel_ccs: dict | None = None,
    channel_automation: dict | None = None,
    programs_json: str = "{}",
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    backend: Any = None,
) -> np.ndarray:
    """Wire parse output and designed instruments through the full render pipeline.

    Builds a note-event schedule, optionally folds CC/pitch-bend automation,
    and drives ``backend`` to produce float32 PCM for the entire song.

    The ``backend`` parameter is injectable so callers (e.g. smoke tests in
    Phase 4) can pass ``FakeBackend()`` without requiring fluidsynth.  When
    ``backend`` is ``None`` a ``FluidSynthBackend`` is constructed lazily —
    which requires fluidsynth and the soundfont to be installed.

    Args:
        score_json: JSON array of section dicts as produced by
            ``build_render_schedule``'s input format (see module docstring).
        instruments_json: JSON string of instrument dicts (currently unused by
            the scheduling layer; reserved for program-select in a future phase).
        channel_ccs: Optional dict mapping channel → ``{cc_num: value}`` for
            initial CC setup.  Passed to the backend via ``cc()`` if provided
            and the backend exposes that method.
        channel_automation: Optional dict mapping channel → list of automation
            descriptor dicts.  Converted to schedule events via
            ``fold_automation_into_schedule``.
        programs_json: JSON object mapping channel (str) → ``[bank, program]``.
            Each entry triggers a ``program_select`` call on the backend (when
            it exposes that method) and sets a ±6-semitone pitch-bend range via
            ``set_pitch_bend_range`` (when that method is present).  Default
            ``"{}"`` leaves channels at their backend defaults so existing
            callers keep working unchanged.
        sample_rate: Audio sample rate in Hz (default 48 000).
        backend: Injectable synth backend.  ``None`` → construct
            ``FluidSynthBackend`` lazily.

    Returns:
        NumPy array of shape ``(total_frames, 2)``, dtype float32.
    """
    if backend is None:
        from libs.music_render.backend import make_fluidsynth_backend
        backend = make_fluidsynth_backend()

    _apply_programs(backend, programs_json)
    _apply_bend_ranges(backend, programs_json, semitones=6)

    schedule_json = build_render_schedule(score_json, sample_rate)

    if channel_automation:
        automation_events = _automation_dicts_to_schedule_events(
            channel_automation, sample_rate
        )
        schedule_json = fold_automation_into_schedule(
            schedule_json, json.dumps(automation_events)
        )

    _apply_initial_ccs(backend, channel_ccs)
    return render_pcm(schedule_json, backend, sample_rate)


def render_audition_pcm(
    section_score_json: str,
    instruments_json: str,
    channel_ccs: dict | None = None,
    channel_automation: dict | None = None,
    programs_json: str = "{}",
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    backend: Any = None,
) -> np.ndarray:
    """Render a short audition clip through the same pipeline as the full song.

    Identical to ``render_song_pcm`` in structure — same backend seam, same
    automation folding — so the WYHIWYG guarantee holds: the audition and the
    final render use exactly one synthesis path.

    Args:
        section_score_json: JSON array of section dicts for the audition clip
            (typically one short section produced by the instruments pane).
        instruments_json: JSON string of instrument dicts (reserved for
            program-select in a future phase).
        channel_ccs: Optional per-channel initial CC dict.
        channel_automation: Optional per-channel automation descriptor dict.
        programs_json: JSON object mapping channel (str) → ``[bank, program]``.
            Forwarded to ``render_song_pcm``.  Default ``"{}"`` so existing
            callers keep working unchanged.
        sample_rate: Audio sample rate in Hz (default 48 000).
        backend: Injectable synth backend.  ``None`` → construct
            ``FluidSynthBackend`` lazily.

    Returns:
        NumPy array of shape ``(total_frames, 2)``, dtype float32.
    """
    return render_song_pcm(
        score_json=section_score_json,
        instruments_json=instruments_json,
        channel_ccs=channel_ccs,
        channel_automation=channel_automation,
        programs_json=programs_json,
        sample_rate=sample_rate,
        backend=backend,
    )


def encode_flac(pcm: np.ndarray, sample_rate: int, path: str) -> None:
    """Encode float32 stereo PCM to a 24-bit FLAC file.

    Uses ``soundfile`` (lazy import) so the module can be imported even if
    soundfile is not installed — the ``ImportError`` is raised only at call time.

    Args:
        pcm: NumPy array of shape ``(n_frames, 2)``, dtype float32.
        sample_rate: Audio sample rate in Hz.
        path: Destination file path for the FLAC output.

    Raises:
        ImportError: If ``soundfile`` is not installed.
    """
    import soundfile as sf  # lazy — soundfile not available in the test sandbox
    sf.write(path, pcm, sample_rate, subtype='PCM_24')


def _apply_initial_ccs(backend: Any, channel_ccs: dict | None) -> None:
    """Send initial CC values to the backend for each channel.

    Called after the backend is constructed and before ``render_pcm`` so that
    all channels are correctly configured (volume, pan, reverb, etc.) before any
    note events fire.  Silently skips channels for which the backend does not
    expose a ``cc`` method.

    Args:
        backend: Synth backend (may or may not expose a ``cc`` method).
        channel_ccs: Dict mapping channel (int) → ``{cc_num: value}``, or None.
    """
    if not channel_ccs or not hasattr(backend, "cc"):
        return
    for channel, ccs in channel_ccs.items():
        for cc_num, value in ccs.items():
            backend.cc(int(channel), int(cc_num), int(value))


def _apply_programs(backend: Any, programs_json: str) -> None:
    """Call program_select on the backend for each channel in programs_json.

    Args:
        backend: Synth backend (may or may not expose a ``program_select`` method).
        programs_json: JSON object mapping channel (str) → ``[bank, program]``.
    """
    if not hasattr(backend, "program_select"):
        return
    programs = json.loads(programs_json)
    for channel_str, (bank, program) in programs.items():
        backend.program_select(int(channel_str), int(bank), int(program))


def _apply_bend_ranges(backend: Any, programs_json: str, semitones: int) -> None:
    """Call set_pitch_bend_range on the backend for each channel in programs_json.

    Only channels present in programs_json receive a bend-range update.
    The range is locked at ±``semitones`` semitones per design/midi-values.md.

    Args:
        backend: Synth backend (may or may not expose ``set_pitch_bend_range``).
        programs_json: JSON object mapping channel (str) → ``[bank, program]``.
            Used only to enumerate the channels that need bend-range setup.
        semitones: Pitch-bend range in semitones to apply (typically 6).
    """
    if not hasattr(backend, "set_pitch_bend_range"):
        return
    programs = json.loads(programs_json)
    for channel_str in programs:
        backend.set_pitch_bend_range(int(channel_str), semitones)


def render_song_and_report_setup(
    score_json: str,
    instruments_json: str,
    programs_json: str,
    channel_ccs_json: str,
    channel_automation_json: str,
    sample_rate: int,
) -> str:
    """Render over a fresh FakeBackend and return JSON of the recorded channel setup.

    Parses the JSON-string arguments (``channel_ccs_json`` and
    ``channel_automation_json`` may be ``"null"`` or ``"{}"``) and drives the
    full ``render_song_pcm`` pipeline over a fresh ``FakeBackend``.  After
    rendering, reads the FakeBackend's recorded state via its accessors and
    returns it as a sorted-keys JSON string.

    This is a test/report helper: it lets test plans assert program-select,
    bend-range, and CC/automation routing without FluidSynth.

    Args:
        score_json: JSON array of section dicts (same format as ``render_song_pcm``).
        instruments_json: JSON string of instrument dicts (forwarded, currently unused).
        programs_json: JSON object mapping channel (str) → ``[bank, program]``.
        channel_ccs_json: JSON object mapping channel (str) → ``{cc_num: value}``,
            or ``"null"`` / ``"{}"``.
        channel_automation_json: JSON object mapping channel (str) → list of
            automation descriptor dicts, or ``"null"`` / ``"{}"``.
        sample_rate: Audio sample rate in Hz.

    Returns:
        JSON string (sorted keys for determinism) encoding::

            {
                "programs":    {channel_str: [bank, program], ...},
                "bend_ranges": {channel_str: semitones, ...},
                "final_ccs":   {channel_str: {cc_str: value, ...}, ...},
            }
    """
    from libs.music_render.backend import FakeBackend

    channel_ccs: dict | None = json.loads(channel_ccs_json)
    channel_automation: dict | None = json.loads(channel_automation_json)

    # JSON object keys are always strings; convert channel keys to int for
    # _apply_initial_ccs and _automation_dicts_to_schedule_events.
    if channel_ccs:
        channel_ccs = {int(k): v for k, v in channel_ccs.items()}
    if channel_automation:
        channel_automation = {int(k): v for k, v in channel_automation.items()}

    fake = FakeBackend()
    render_song_pcm(
        score_json=score_json,
        instruments_json=instruments_json,
        channel_ccs=channel_ccs,
        channel_automation=channel_automation,
        programs_json=programs_json,
        sample_rate=sample_rate,
        backend=fake,
    )

    programs_report = {
        str(ch): list(prog)
        for ch, prog in fake._programs.items()
    }
    bend_ranges_report = {
        str(ch): semis
        for ch, semis in fake._bend_ranges.items()
    }
    final_ccs_report = {
        str(ch): {str(cc): val for cc, val in ccs.items()}
        for ch, ccs in fake._ccs.items()
    }

    result = {
        "programs": programs_report,
        "bend_ranges": bend_ranges_report,
        "final_ccs": final_ccs_report,
    }
    return json.dumps(result, sort_keys=True)


def _automation_dicts_to_schedule_events(
    channel_automation: dict,
    sample_rate: int,
) -> list[dict]:
    """Convert per-channel automation descriptor dicts to schedule event dicts.

    Each automation descriptor carries a curve (stored as a dict produced by
    ``slider_2d.to_dict``) and a repeat_s.  This function reconstructs each
    dict into a ``Curve`` object via ``slider_2d.from_dict``, then samples it
    at the start of every 512-frame chunk across the curve's full period,
    emitting one CC or bend event per chunk boundary where the value changes.
    This mirrors the chunk-boundary automation sampling used by the real-time
    path.

    Args:
        channel_automation: Dict mapping channel (int) → list of automation
            descriptor dicts (see ``music_web_instruments.channel_automation_from_designed``).
        sample_rate: Audio sample rate in Hz.

    Returns:
        List of schedule-event dicts (``{"type": "cc"|"bend", "frame": int,
        "channel": int, "cc": int, "value": int}``).
    """
    from libs.slider_2d import sample as curve_sample, from_dict as curve_from_dict

    CHUNK = 512
    events: list[dict] = []

    for channel, specs in channel_automation.items():
        for spec in specs:
            curve_dict = spec.get("curve")
            if curve_dict is None:
                continue
            curve = curve_from_dict(curve_dict)
            repeat_s = float(spec.get("repeat_s", 1.0))
            total_frames = int(round(repeat_s * sample_rate))
            last_value: int | None = None
            frame = 0
            while frame < total_frames:
                phase = (frame / total_frames) if total_frames > 0 else 0.0
                raw_value = curve_sample(curve, phase)
                if spec["target"] == "cc":
                    int_value = int(round(max(0.0, min(127.0, raw_value))))
                    if int_value != last_value:
                        events.append({
                            "type": "cc",
                            "frame": frame,
                            "channel": int(channel),
                            "cc": int(spec["cc"]),
                            "value": int_value,
                        })
                        last_value = int_value
                else:  # bend
                    bend_range = float(spec.get("bend_range", 6))
                    int_value = int(round(
                        max(-8192.0, min(8191.0,
                            raw_value / bend_range * 8192.0))
                    ))
                    if int_value != last_value:
                        events.append({
                            "type": "bend",
                            "frame": frame,
                            "channel": int(channel),
                            "value": int_value,
                        })
                        last_value = int_value
                frame += CHUNK

    return events


def _dispatch_event(backend: Any, event: dict) -> None:
    """Send one schedule event to the backend."""
    kind = event["type"]
    if kind == "note_on":
        backend.note_on(event["note"], event["velocity"], event["channel"])
    elif kind == "note_off":
        backend.note_off(event["note"], event["channel"])
    elif kind == "cc":
        if hasattr(backend, "cc"):
            backend.cc(event["channel"], event["cc"], event["value"])
    elif kind == "bend":
        if hasattr(backend, "pitch_bend"):
            backend.pitch_bend(event["channel"], event["value"])


def render_and_report_channels(schedule_json: str, sample_rate: int) -> str:
    """Render a schedule over a fresh FakeBackend and return JSON of the sorted
    list of MIDI channels that received a note-on. Lets test plans assert routing.

    Args:
        schedule_json: JSON string of the schedule (as returned by
            ``build_render_schedule`` / ``fold_automation_into_schedule``).
        sample_rate: Audio sample rate in Hz.

    Returns:
        JSON string encoding the sorted list of channel numbers that received at
        least one note-on during the render (e.g. ``"[0, 1]"``).
    """
    from libs.music_render.backend import FakeBackend
    fake = FakeBackend()
    render_pcm(schedule_json, fake, sample_rate)
    return json.dumps(fake.note_on_channels())
