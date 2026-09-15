# music_render

Offline audio render pipeline for the music web app.  Converts a parsed score
into raw float32 PCM without touching the GUI: it builds a sample-accurate
event schedule, optionally folds in CC/pitch-bend automation, then drives a
pluggable synth backend to produce stereo float32 PCM.

## Backend interface

Any object passed as `backend` to `render_pcm` must implement the following methods:

| Method | Signature | Description |
|---|---|---|
| `note_on` | `(note: int, velocity: int, channel: int) -> None` | Activate a MIDI note (0–127) at the given velocity (0–127) on the given MIDI channel (0–15). |
| `note_off` | `(note: int, channel: int) -> None` | Deactivate a MIDI note on the given channel. |
| `render_block` | `(n_frames: int) -> np.ndarray` | Render exactly `n_frames` frames of interleaved stereo float32 PCM (shape `(n_frames, 2)`). Called after all note_on/note_off events for the block have been dispatched. |

Optional methods (called only when present via `hasattr`):

| Method | Signature | Description |
|---|---|---|
| `cc` | `(channel: int, cc_number: int, value: int) -> None` | Send a MIDI CC message. |
| `pitch_bend` | `(channel: int, value: int) -> None` | Send a MIDI pitch-bend message (−8192..+8191). |
| `program_select` | `(channel: int, bank: int, program: int) -> None` | Select a bank/program on a channel. |
| `set_pitch_bend_range` | `(channel: int, semitones: int) -> None` | Configure the pitch-bend range via RPN. |

`FakeBackend` is the reference implementation of this interface (no external
dependencies; deterministic output).  Notes are keyed by `(channel, note)` so
the same MIDI note number on different channels is tracked independently.
`make_fluidsynth_backend` (Phase 3) returns a FluidSynth-backed object that
satisfies the same interface.

### FakeBackend accessors (for testing)

| Accessor | Returns |
|---|---|
| `active_notes()` | Sorted list of distinct MIDI note numbers currently active (across all channels). |
| `active_channel_notes()` | Sorted list of `(channel, note)` tuples currently active. |
| `note_on_channels()` | Sorted list of every channel that ever received a `note_on`. |
| `program_for(channel)` | `(bank, program)` tuple for channel, or `None`. |
| `cc_value(channel, cc_number)` | Last CC value for channel+cc_number, or `None`. |
| `bend_for(channel)` | Last pitch-bend value for channel, or `None`. |
| `bend_range_for(channel)` | Pitch-bend range in semitones for channel, or `None`. |

## Score JSON format

`build_render_schedule` accepts a JSON array of section dicts.  Each section
dict has an `events` key holding a list of note-event dicts:

```json
[
  {
    "events": [
      {"time": 0.0, "duration": 1.0, "midi_note": 60, "velocity": 80, "channel": 0}
    ]
  }
]
```

Fields: `time` (float, seconds from section start), `duration` (float, seconds),
`midi_note` (int, 0–127), `velocity` (int, 0–127), `channel` (int, 0–15).

## Public API

### Schedule / PCM layer (`schedule.py`)

| Symbol | Status | Description |
|---|---|---|
| `build_render_schedule(score_json, sample_rate) -> str` | real | Build a sample-accurate JSON event schedule from a parsed score. |
| `fold_automation_into_schedule(schedule_json, automation_json) -> str` | real | Merge CC/pitch-bend automation points into a schedule, sorted by frame. |
| `render_pcm(schedule_json, backend, sample_rate) -> np.ndarray` | real | Drive `backend` with the schedule; return `(n_frames, 2)` float32 PCM. |
| `render_pcm_with_fake_backend(schedule_json, sample_rate) -> np.ndarray` | real | Convenience: render with a fresh `FakeBackend` (deterministic, no dependencies). |
| `render_and_report_channels(schedule_json, sample_rate) -> str` | real | Render over a fresh `FakeBackend`; return JSON list of channels that received a note-on. |
| `render_song_pcm(score_json, instruments_json, ...) -> np.ndarray` | real | Full-song wiring with injectable backend (default: FluidSynthBackend, lazy). Accepts `programs_json` to select bank/program per channel and lock pitch-bend range to ±6 semitones. |
| `render_audition_pcm(section_score_json, instruments_json, ...) -> np.ndarray` | real | Short audition wiring; same pipeline as `render_song_pcm`. Forwards `programs_json`. |
| `encode_flac(pcm, sample_rate, path) -> None` | real | 24-bit FLAC encode via soundfile (lazy import). |
| `render_song_and_report_setup(score_json, instruments_json, programs_json, channel_ccs_json, channel_automation_json, sample_rate) -> str` | real | Render over a fresh `FakeBackend`; return JSON of recorded channel setup (programs, bend_ranges, final_ccs). Test/report helper — no FluidSynth required. |

### Render entry points (`schedule.py`)

| Symbol | Status | Description |
|---|---|---|
| `render_song_pcm(score_json, instruments_json, ...)` | real | Full-song wiring: parse output + designed instruments → schedule → fold → render. Backend is injectable (default: `FluidSynthBackend` built lazily). Accepts `programs_json` (JSON object `{channel: [bank, program]}`) to set bank/program per channel and lock pitch-bend range to ±6 semitones. |
| `render_audition_pcm(section_score_json, instruments_json, ...)` | real | Short audition wiring: same pipeline, shorter clip. Accepts and forwards `programs_json`. |
| `encode_flac(pcm, sample_rate, path)` | real | Encode float32 PCM to 24-bit FLAC via `soundfile` (lazy import). |
| `render_and_report_channels(schedule_json, sample_rate) -> str` | real | Render over a fresh `FakeBackend` and return JSON of the sorted list of MIDI channels that received a note-on. Used by test plans to assert per-channel routing. |
| `render_song_and_report_setup(score_json, instruments_json, programs_json, channel_ccs_json, channel_automation_json, sample_rate) -> str` | real | Render over a fresh `FakeBackend` and return JSON of the recorded channel setup: `{"programs": {ch: [bank,prog]}, "bend_ranges": {ch: semis}, "final_ccs": {ch: {cc: value}}}`. Lets test plans assert program-select, bend-range, and CC/automation routing without FluidSynth. |

### `programs_json` parameter

Both `render_song_pcm` and `render_audition_pcm` accept a `programs_json: str = "{}"` keyword argument. It is a JSON object mapping MIDI channel (as a string key) to `[bank, program]`:

```json
{"0": [0, 40], "1": [0, 25]}
```

For each channel in the map the pipeline calls (when the backend exposes the method):
1. `backend.program_select(channel, bank, program)` — selects the GM patch.
2. `backend.set_pitch_bend_range(channel, 6)` — locks pitch-bend to ±6 semitones (per `design/midi-values.md`).

The default `"{}"` leaves all channels at backend defaults so existing callers are unaffected.

### GS-NRPN vibrato note (`backend.py`)

`FluidSynthBackend.cc` emits the Roland GS NRPN sequence alongside CC 76 (vibrato rate) and CC 77 (vibrato delay). Some SoundFonts respond to the GS NRPN but not to GM CC 76/77; this duplication ensures vibrato settings reach both code paths. The sequence for CC number `n` is: NRPN MSB CC99=0x01, NRPN LSB CC98=(0x08 if rate else 0x0A), Data Entry MSB CC6=value. `FakeBackend` is a plain recorder and does not duplicate this logic.

### Backend layer (`backend.py`)

| Symbol | Status | Description |
|---|---|---|
| `FakeBackend` | real | Deterministic backend; amplitude = `note/127.0` per active note (clipped sum). |
| `fake_backend_render_note(note, n_frames) -> np.ndarray` | real | Convenience helper: render one note for `n_frames` frames via `FakeBackend`. |
| `FluidSynthBackend` | **untested glue** | Real offline-render backend (float/48 kHz/FLUID_INTERP_HIGHEST). Requires `fluidsynth` (pyfluidsynth) and a 1 GB soundfont — not available in the test sandbox. `fluidsynth` is imported lazily inside `__init__` so the module imports cleanly without it. |
| `make_fluidsynth_backend(soundfont_path) -> FluidSynthBackend` | **untested glue** | Thin factory; same lazy-import guarantee as `FluidSynthBackend`. |

**`FluidSynthBackend` and `make_fluidsynth_backend` are untested by design**
(like `/scene/edit` in the scene engine): they are thin glue between the
fully-tested scheduling / FakeBackend seam and the native FluidSynth library
+ soundfont that are not available in the test sandbox.  All scheduling code
is covered by `test_primary.txt` via `FakeBackend`.

## Test plans

| File | Purpose |
|---|---|
| `test_primary.txt` | Primary plan: full schedule/WAV/render oracles over FakeBackend; all real functions exercised with exact expected values. |
