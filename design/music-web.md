# Music → Web — Design Hub

**Date:** 2026-06-14 (original) · **2026-06-29 corrected · 2026-06-30 rebuilt (see "History" below)**
**Status:** **Feature-complete WYHIWYG port — built & green** (Domain 1/2/3).
The real-FluidSynth branch + `web/static/music_app.js` are untested-by-design;
a manual browser pass (needs fluidsynth + soundfont + soundfile installed) is
still owed before the app can be *heard*.
**Build/rebuild plan:** kept with the frozen kickoff/handoff records in the
private monorepo this app was carved out of (see "See also" below).
**Reference (do not edit):** `design/music_app.md`, `design/instruments.md`,
`design/midi-values.md`, `design/text_format.md`

---

## What this is

A Flask web port of the tkinter `music` app into `web/server.py`, served at
`/music`. The tkinter `bin/music` app is left **completely unchanged**; the two
apps share libs by import only, never share instrument files, and never share
audio-driver state. New code lands exclusively in new files.

---

## Load-bearing decision: WYHIWYG (one synth, one playback path)

**What You Hear Is What You Get** — the instrument auditioned in the editor is
guaranteed *identical* to the instrument in the final render, because there is
exactly **one synthesizer and one playback path**:

- **One synthesizer:** a deterministic **offline FluidSynth render → float PCM**
  is the only source of sound. Audition, song-Play, and FLAC all draw from it.
  A second synthesis path cannot exist, so the editor and the render cannot
  diverge.
- **One playback path: Web Audio.** Both Audition and Play fetch raw float32 PCM
  and play it through an `AudioBuffer` / `AudioBufferSourceNode`. Web Audio takes
  float32 natively (no WAV format tag, no 16-bit conversion), starts instantly,
  and cancel/replaces trivially — which is exactly what audition-on-every-change
  needs.
- The `<audio>` element and the chunked-streaming-WAV transport are **dropped**.
  There is **no transport radio button**: the two former transports replayed the
  same server render, so they sounded identical; Web Audio is simply the more
  reliable/responsive one. (If song length ever makes a single buffer
  impractical, add chunked Web Audio scheduling — not the `<audio>` element.)
- CC/pitch-bend automation curves are **folded into the offline render loop** at
  chunk boundaries (the real-time path's dedup math, driven by elapsed-sample
  time).
- The real-time / PulseAudio path is **not ported**.

**Locked quality settings:**

| Setting | Value |
|---|---|
| Sample format | `fluid_synth_write_float` (internal float) |
| Sample rate | 48 kHz |
| Interpolation | `FLUID_INTERP_HIGHEST` |
| FLAC output depth | 24-bit |
| Default soundfont | `/usr/share/sounds/sf2/MuseScore_General_Full.sf2` |

The web app renders at 48 kHz; the unchanged tkinter app stays at 44.1 kHz —
intentional, since the two apps never share instrument files.

**Locked transports:**

- **Audition** — Web Audio: POST the *edited instrument spec* → server renders a
  short single-instrument preview → raw float32 PCM → `AudioContext` /
  `AudioBufferSourceNode`. Cancel/replace on every control change.
- **Song Play** — Web Audio: POST `{text, caret}` → server renders the actual
  score from the caret with the designed instruments → raw float32 PCM →
  `AudioBufferSourceNode`. **Not** a WAV stream.
- **FLAC** — download of the same PCM encoded 24-bit lossless (`/music/flac`).
- **No WebSockets / Socket.IO. No `<audio>` element.**

---

## Instrument editing (feature-complete)

The web instrument editor reproduces the desktop control set
(`design/midi-values.md`) — it is **not** a reduced subset.

**"Pick" is a live combined editor**, not a one-shot grid. Opening the editor
for a row shows the instrument grid **and** the MIDI-parameter pane together, and
**stays open** so the user can pick another instrument or tweak parameters
without re-opening it. Every value change **auditions that one instrument live**:
a slider on mouse-up, a text box on Enter or blur, a grid pick on click. The
audition POSTs the current spec to `/music/audition` and plays it via Web Audio,
cancel/replacing any in-flight preview.

**Control set** (per `design/midi-values.md`; persisted to
`appdata/webmusicdata/instruments.json`):

- **Per-note:** Name, Pitch (chord-capable; locked to the kit abbreviation for
  percussion), Velocity (0–127, default 100), Duration (0–4 s, default 1.0).
- **Instrument pick:** GM 16×8 melodic grid + percussion-kit row + 7×7 drum
  sub-grid (reuses the existing grid builders).
- **Channel basics:** Volume (CC7), Pan (CC10, default 64), Reverb (CC91,
  default 40), Chorus (CC93, default 0).
- **2D curves** (each a `curve2d.mjs` thumbnail opening `openCurveEditor`, with a
  **per-curve repeat-interval** add-on): Expression (CC11, default flat 127),
  Brightness (CC74, default flat 64), Bend (±6 semitones, default flat 0 — via
  the standard pitch-bend-range RPN).
- **Vibrato:** Depth (CC1), Rate (CC76), Delay (CC77). Rate/Delay are also sent
  as Roland GS NRPNs (MSB 0x01, LSB 0x08 rate / 0x0A delay) because most GM
  SoundFonts ignore CC76/77 — audibility depends on the SoundFont honouring GS
  NRPNs. (Lives in the untested-by-design real-synth glue.)
- **ADSR:** Attack (CC73), Decay, Sustain, Release (CC72).

The in-page **docked panel + modal editor** replace the tkinter floating-window
machinery (overrideredirect / clamp / flip / drag / focus-out) and the
cross-process file-mtime sync, which are dropped (single server process).
Instruments persist to `appdata/webmusicdata/instruments.json` — a **new path** so the
two apps never fight over `appdata/musicdata/instruments.json`.

The editor text is persisted **on disk** in `appdata/webmusicdata/scores/` (see the
file-handling section below); `localStorage` is no longer used anywhere.

> **Superseded by `design/music-web-files.md` (built & green 2026-07-02).** The
> former `localStorage` score persistence has been **removed** in favor of
> disk-backed score files (`appdata/webmusicdata/scores/`) with a File-cluster switcher,
> a name-first **New** affordance (no Save button — autosave-on-everything), and
> a `session.json` current-file + caret/scroll memory, reaching file-handling
> parity with the desktop app. Spec: `design/music-web-files.md`.

---

## Render pipeline routes per channel

The render pipeline is channel-aware (this was the central defect; see
"History — dated correction log" item 2 below):

- `build_render_schedule` carries a per-event MIDI `channel`; `_dispatch_event`
  routes note-on/note-off/cc/pitch-bend to `event["channel"]`.
- The backend interface carries the channel: `note_on(note, velocity, channel)`,
  `note_off(note, channel)`, `cc(channel, n, v)`, `pitch_bend(channel, v)`,
  `program_select(channel, bank, program)`, `set_pitch_bend_range(channel,
  semitones)`.
- `render_song_pcm` / `render_audition_pcm` accept a **programs** map
  (channel→(bank, program)) and call `program_select` per channel and
  `set_pitch_bend_range(channel, 6)` before rendering, apply the per-channel
  initial CCs, and fold automation curves into the schedule.
- For **Play**, the programs map is the parser's `score.programs` (single source
  of truth). For **Audition**, it is the one edited instrument on channel 0.
- `FakeBackend` records all of this per channel (keyed by `(channel, note)`) and
  exposes accessors, so routing/program/CC/bend behaviour is covered by Domain-1
  oracles without FluidSynth. The real `FluidSynthBackend` is untested-by-design.

---

## New libraries

### `libs/music_web_instruments/`

Pure instruments model — no FluidSynth, no tkinter, no `music/` imports.

| Function | Purpose |
|---|---|
| `build_channel_ccs(patch_json)` | Map a patch (JSON) to initial MIDI CC values for its channel |
| `instruments_arg_from_designed(rows_json)` | Build the `instruments` arg passed to `parse_score` |
| `channel_ccs_from_designed(rows_json, channels_json)` | Per-channel initial CC dict from designed instruments |
| `channel_automation_from_designed(rows_json, channels_json)` | Per-channel automation list (curve refs) from designed instruments |
| `play_offset_for_caret(text, caret_offset)` | Section name + sample offset for Play-from-cursor semantics |
| `audition_score_and_programs(spec_json)` | One edited instrument spec + pitch(es)/vel/dur → `{score, instruments, programs, channel_ccs, channel_automation}` for `render_audition_pcm` |

All dict-shaped boundary parameters flow as JSON strings (plan-file grammar has
no dict literal; matches the route's JSON flow). Domain: 1 (harness-tested).

### `libs/music_render/`

WYHIWYG scheduling / render orchestration via an injected synth backend.

| Symbol | Purpose |
|---|---|
| `build_render_schedule(events, programs, sample_rate)` | Sample-accurate, **channelled** note-on/note-off schedule |
| `fold_automation_into_schedule(schedule, automation, sample_rate)` | Fold CC/bend curves into the schedule at chunk boundaries |
| `render_pcm(schedule, backend, sample_rate)` | Drive the injected backend; returns float PCM |
| `render_song_pcm(...)` | Full-song wiring: parse output + designed instruments + **programs** → schedule → fold → render |
| `render_audition_pcm(...)` | Single-instrument preview wiring; same path |
| `render_and_report_channels(schedule, sample_rate)` | Test helper: which channels saw note-ons (makes routing plan-assertable) |
| `FakeBackend` | Deterministic, channel-recording test backend (no FluidSynth). Not a stub. |
| `FluidSynthBackend` | Thin glue: float / 48 kHz / FLUID_INTERP_HIGHEST / per-channel program/CC/bend/RPN/GS-NRPN. **Untested by design.** |

WAV framing (`wav_header` / `wav_byte_length`) is **removed** — the single Web
Audio transport ships raw float32 PCM, so no WAV header is produced anywhere.

---

## Web blueprint

**File:** `web/apps/music.py` · **Factory:** `make_blueprint(project_root,
soundfont=DEFAULT_SF)` · **Asset root:** `appdata/webmusicdata/` (path-gated via
`libs/web_runtime`).

### Routes

| Route | Method | Purpose | Tested |
|---|---|---|---|
| `/music` | GET | Serve `web/music.html` | Domain 2 smoke |
| `/music/parse` | POST `{text}` | `parse_score` with designed instruments; sections/events/errors/beat-counts | Domain 2 smoke |
| `/music/instruments` | GET | Read `appdata/webmusicdata/instruments.json` | Domain 2 smoke |
| `/music/instruments` | POST | Write `appdata/webmusicdata/instruments.json` atomically; path-guarded | Domain 2 smoke |
| `/music/audition` | POST `{spec}` | Render single-instrument preview via `render_audition_pcm`; return raw float32 PCM; injectable backend | Domain 2 smoke (FakeBackend) |
| `/music/play` | POST `{text, caret}` | Render the score from the caret with designed instruments via `render_song_pcm`; return raw float32 PCM | Domain 2 smoke (FakeBackend) |
| `/music/flac` | GET | Full-song 24-bit FLAC download via `encode_flac`; **untested by design** | untested by design |
| `/music/scores` | GET | List `.music`/`.txt` files + session current file & caret/scroll view state | Domain 2 smoke |
| `/music/score` | GET `?name=` | Read one score's text (400 invalid name, 404 missing) | Domain 2 smoke |
| `/music/score` | PUT `{name,text,make_current?}` | Atomic-write a score (autosave); optional session update | Domain 2 smoke |
| `/music/scores/new` | POST `{name,text?}` | Create a named score & set it current (400 no name, 409 collision) | Domain 2 smoke |
| `/music/session` | POST `{current?,caret?,scroll?}` | Merge current/caret/scroll into `session.json` (404 missing current) | Domain 2 smoke |

The file routes resolve every path through `resolve_in_root(project_root,
score_rel_path(name))` (pure guard in `libs/music_scores/`) and write via
`_atomic_write_text`. See `design/music-web-files.md` for the full contract.

**Injectable-backend seam:** `_select_backend(request)` returns `FakeBackend()`
when `backend == "fake"`, else `make_fluidsynth_backend(soundfont)` lazily. The
real-FluidSynth branch is exempt from smoke by design (native lib + large
soundfont absent from the sandbox); all framing/scheduling/routing stays behind
the `FakeBackend` seam and IS covered.

**Smoke tests:** `web/smoke/music.py`.

---

## Static assets

| File | Domain | Purpose |
|---|---|---|
| `web/music.html` | browser-only | Page shell: score editor, toolbar (File-cluster switcher + New, then Play/Stop/FLAC), parse strip, docked instruments panel + live Pick editor |
| `web/static/music_app.js` | browser-only, untested | Editor with **disk-backed** score files (switcher / name-first New / short-debounce autosave + caret-scroll session autosave + `keepalive` exit-flush, no `localStorage`); Web Audio Audition + Play transports; live Pick/MIDI editor; instruments panel; persist via `/music/instruments`. Untested by design (DOM + AudioContext). |
| `web/static/music_audition.mjs` | Domain 3 | Pure PCM buffer math (byte↔frame, deinterleave, fade, cancel/replace) |
| `web/static/music_audition.test.mjs` | Domain 3 | `node:test` unit tests, zero npm deps |

There is **no** `<audio>` element and **no** `music_caret.mjs` — the server-side
`play_offset_for_caret` is the single source of truth for caret → play offset.

---

## Asset directory

`appdata/webmusicdata/` — a top-level asset directory. Holds
`appdata/webmusicdata/instruments.json` (the web app's designed instruments),
`appdata/webmusicdata/scores/` (the `.music`/`.txt` score files, with a `.gitkeep`),
`appdata/webmusicdata/session.json` (`{version, last_file, caret, scroll}` — the current
score's *relative name* + view state, never an absolute path), and `.gitkeep`.
`appdata/musicdata/` (tkinter app) and the repo-root `.music` files are **never** read
or written by the web app.

---

## Tkinter app is unchanged

`music/`, `music.py`, `music_player.py`, `instruments_pane.py`,
`instruments_store.py`, `session_store.py`, and `appdata/musicdata/` are **not
modified**. The web app re-implements the instrument-model math in
`libs/music_web_instruments/` — it reads `music/` for reference, ports the math,
and never imports from `music/`.

---

## Testing map

| Artifact | Domain | Runner |
|---|---|---|
| `libs/music_web_instruments` | 1 | `bin/dev test` |
| `libs/music_render` (schedule/routing/FakeBackend) | 1 | `bin/dev test` |
| `libs/music_render` FluidSynthBackend / render entry | untested glue | — |
| `web/apps/music.py` routes | 2 | `bin/dev test` (`web/smoke/music.py`) |
| `web/apps/music.py` `/music/flac` | untested by design | — |
| `web/static/music_audition.mjs` | 3 | `bin/dev test` (`*.test.mjs`) |
| `web/static/music_app.js`, `web/music.html` | browser-only, manual | — |

Verify with `./bin/green` (diff-since-green + `bin/dev test` exit code +
commit-on-green). The real-audio path needs `pyfluidsynth` + `libfluidsynth` +
a soundfont + `python-soundfile` and a manual browser pass to be *heard* — this
is the step the original port skipped.

---

## See also

- `design/music_app.md` — the tkinter app this ports (reference, do not edit).
- `design/music-web-files.md` — **built & green** disk-backed score files (open /
  switch / name-first new / autosave-on-everything / caret+scroll last-file
  memory); replaced `localStorage` score persistence to reach file-handling
  parity with the desktop app.
- `design/instruments.md`, `design/midi-values.md` — the desktop instrument
  editor and its control table (reference, do not edit).
- The executable rebuild plan and the frozen kickoff/handoff records stay in
  the private monorepo this app was carved out of.

---

## History — dated correction log (archival)

### ⚠ Correction (2026-06-29): the original port was not feature-complete

The first build of this app was declared "Phase 5 complete — Finalized," but it
was **not** a feature-complete port, and several pieces were broken or never
connected. This is recorded here (not only in a handoff) because the design doc
is the source of truth, and the mistake shaped the corrected design below.

What was wrong (all confirmed in the shipped code):

1. **Audition and Play ignored the user's score.** Both routes rendered a
   hardcoded single middle-C (`_AUDITION_SCORE_JSON`) and ignored the editor
   text *and* the designed instruments (`instruments_json="{}"`, no CCs, no
   programs).
2. **The render pipeline ignored instruments entirely.** `_dispatch_event`
   hardcoded MIDI channel 0; `render_song_pcm` never called `program_select`;
   `FluidSynthBackend.note_on/note_off` hardcoded channel 0. Net effect:
   everything played as Acoustic Grand Piano on channel 0 regardless of the
   instrument, volume, or pan chosen. Instrument selection was cosmetic.
3. **The MIDI-parameter editor was unreachable.** `openCurveEditor` existed in
   `music_app.js` but was wired to no control.
4. **The instrument editor was descoped.** The kickoff reduced the desktop
   editor (`design/midi-values.md`: Vel/Dur, Reverb/Chorus, Expression/
   Brightness/Bend 2D curves, Vibrato, ADSR, and **audition-on-every-change**)
   to "GM grid + percussion grid + curve popups," and even that curve editor was
   left unwired. Most controls were never built.
5. **The streaming WAV was malformed.** `wav_header` advertised integer PCM
   (format tag 1) for 32-bit **float** data → `<audio>` played silence/garbage.
6. **The editor was not persisted** → any page reload wiped the typed score.

**Root cause.** The port was built test-first behind a fake-synth seam, so the
green gate only ever certified the framing/scheduling math (which is silent by
construction). The one check that separates "passes tests" from "works" —
hearing it in a browser — was deferred to a manual pass that the sandbox (no
`fluidsynth`, no soundfont, no `customtkinter`) made impossible, and it was
never performed. "Finalized" was asserted against a routes-and-libs checklist,
not against desktop feature parity or any audible result.

The corrected architecture below is the source of truth. The original
kickoff and handoff documents that preceded it are retained **frozen** as
historical record in the source monorepo and are not edited, but are not
published in this repository.
