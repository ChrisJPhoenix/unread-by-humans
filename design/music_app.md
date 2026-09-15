# Music App — Overall Architecture

This tkinter front-end's own code is **not** included in this repository (it
needs `customtkinter`, `pyfluidsynth`, and `pulseaudio`). The doc ships because
the web app in this repository is a port of the tkinter app, performed by
Claude from this document, and because the score-text libraries are shared by
both.

## Files

| File | Role |
|---|---|
| `music.py` | CustomTkinter GUI, button handlers, keybindings |
| `libs/music_parser/` | Text → `ParsedScore` data structure, plus `event_grid`/`section_beat_count` inspection helpers |
| `libs/pitch/` | Note-designator / chord-string → MIDI; shared by the parser and the instruments pane |
| `music_player.py` | Fluidsynth real-time playback + offline FLAC render |
| `instruments_pane.py` | Floating instrument-configuration window (see `design/instruments.md`) |
| `instruments_store.py` | File-I/O helpers for `appdata/musicdata/instruments.json` |
| `session_store.py` | remembers last-used file (`appdata/musicdata/session.json`) |

**Libraries:** `music_parser`, `music_render`, `music_scores`,
`music_web_instruments`, `pitch`, `tempo_map` (the full cluster map stays in
the private monorepo this app was carved out of).

## Data flow

```
editor text
    │
    ▼ parse_score(text, instruments=instruments_arg_from_designed)   # from libs.music_parser import parse_score
ParsedScore
  ├── sections: dict[name → Section]
  │     ├── tracks: list[Track]   (instrument lines + include sequences)
  │     └── beat_count: float     (max of all track durations, in quarter beats)
  ├── events: list[NoteEvent]     (flat, absolute-time, per section)
  ├── programs: dict[channel → (bank, prog)]
  ├── instrument_channels: dict[lowercase name → channel]
  └── errors: list[ParseError]   (section, line, col, message)
    │
    ├──▶ MusicPlayer.play(section, programs,
    │         channel_ccs=channel_ccs,         # per-channel initial CC values
    │         channel_automation=channel_automation)  # continuous 2D-curve automation
    │       real-time via pulseaudio
    │       Automation sends are deduped: a CC or pitch-bend is transmitted only
    │       when its integer value changes since the last send for that
    │       channel+target (helpers in libs/slider_2d/automation_send.py).
    │       stop() cancels in-flight audition automation threads via _stop event
    │       (previously only supersession or duration completion ended them).
    └──▶ MusicPlayer.render_to_flac(section, programs, path,
              channel_ccs=channel_ccs)         # static CCs applied; curves deferred
            offline via fluid_synth_write_s16
```

## Instrument → MIDI channel assignment

Channels are assigned dynamically; the user never picks a channel. Multiple
lines with the same instrument name share one MIDI channel. Channels are
allocated in order of first appearance, drawing from the full pool of 16
(channel 10 is no longer reserved for percussion — percussion is selected
by instrument, not by channel). The only illegal condition is needing more
than 16 channels at once; the parser reports it on the offending instrument
line.

Song instrument names are resolved case-insensitively against the user's
designed instruments from the Instruments pane (program/bank + initial
channel CCs + continuous 2D-curve automation during real-time playback).
FLAC render applies program/bank + static CCs; automation curves are
deferred.  When a song instrument name is not found in the designed
instruments, the parser falls back to the built-in demo instrument set
unchanged.

The `instruments` argument passed to `parse_score` (built by
`MusicApp._instruments_arg_from_designed` from `InstrumentsPane.designed_instruments`)
maps lowercase name → a spec dict `{"bank", "program", "is_percussion",
"drum_note"}`. (The parser also still accepts a legacy `(bank, program)` 2-tuple
for melodic instruments.) When `is_percussion` is true, that instrument's score
lines use the anchor-free `z`-hit syntax (`design/text_format.md` §Percussion
lines): each `z` emits a `NoteEvent` whose `midi_note` is the selected `drum_note`,
on the instrument's allocated channel with the kit bank/program already in
`programs[ch]` — no MIDI-channel-10 special-casing, so playback reproduces the
same sound auditioned in the pane.

## Play button

Plays the section containing the cursor, starting from the note event whose
token the cursor is on (or the beginning if cursor is not on a note).

## FLAC render

Renders the topmost (first) section in the file from time 0 in full using a
software-only synth (no audio driver, bypasses real-time scheduling).

Offline synths are drawn from a lazily-grown pool (`_offline_synth_pool`).
The first render for a given soundfont pays the `sfload` cost; subsequent
renders reuse a pooled synth with the soundfont already loaded, calling
`system_reset()` between uses so no state leaks between renders. The pool
grows on demand and never shrinks. Concurrent renders each get their own
synth from the pool.

All synths — real-time and offline — are created with a raised polyphony
ceiling (`_SYNTH_POLYPHONY = 1024`, up from FluidSynth's default of 256) so
a transient render backlog cannot overflow the rvoice ring buffer.

## Startup file / last-file memory

A command-line path (positional `file` argument, or a bare path with no
subcommand) takes precedence and is remembered in `appdata/musicdata/session.json`.
Otherwise the app auto-reloads the last-used file at startup if the path
still exists on disk. Only the file path is remembered; cursor position,
scroll offset, and pane state are not restored.

## Error policy

- Sections with parse errors are flagged but do not prevent other sections
  from playing, provided those sections do not transitively include the broken one.
- Cycle detection: report a parse error on the include line that closes the cycle.
- Future: red-background highlighting for error tokens in the editor.

## See also

- `design/music-web.md` — web port of this app (Flask / WYHIWYG offline render; tkinter app unchanged).
