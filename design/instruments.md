# Instruments Pane — Design

**Date:** 2026-05-29
**Status:** Phase 4 built — cross-process focus-sync added (FocusIn/FocusOut on
pane and main window call sync_if_file_changed, which is mtime-gated). Phase 3:
persistence added (save-on-change, load-on-startup, create-at-startup-withdrawn
lifecycle). Phase 2: audition built (collapsed strip, expanded panel with 1D
sliders + 2D curve sliders + RepeatSlider, instrument grid popup, percussion
7×7 sub-grid, dynamic channel allocation with release, error highlighting,
curve-over-time automation during audition).

---

## Purpose

A floating window for configuring instruments used in the score and hearing
the result of MIDI parameter changes immediately. The authoritative spec for
the controls — their labels, ranges, GM defaults, and CC numbers — is
`design/midi-values.md`. This document describes what is actually built and how the pieces are wired;
it does not re-list the control table.

---

## Trigger and window lifecycle

- An **Instruments** button is added to the right of the existing toolbar
  buttons (after "Save FLAC", before the status label, separated by a thin
  divider).
- The `InstrumentsPane` is **created immediately at app startup** (via
  `_create_instruments_pane`) and immediately `withdraw()`n so it does not
  flash a visible window.  This ensures saved instruments load at startup and
  the pane object is always available (needed for cross-process sync).
- Clicking **Instruments** calls `deiconify() + lift()` on the existing pane.
  If the pane was somehow destroyed, it is recreated first.
- Closing the window **hides** it (`withdraw`) rather than destroying it, so
  rows survive close/reopen within a session.
- `MusicApp` holds a single `_instruments_pane` reference; always non-None
  after `__init__`.

---

## Layout

```
InstrumentsPane (CTkToplevel, 1100×550 initial, min 800×240)
├── top_bar (CTkFrame, row 0)
│   └── Add (CTkButton) — appends one InstrumentRow to the scroll area
├── scroll_area (CTkScrollableFrame, row 1, fills remaining space)
│   └── InstrumentRow × N   (each packed fill="x", stacked vertically)
└── status_label (CTkLabel, row 2) — window-level status bar
```

The **status bar** is a single window-level `CTkLabel` at the bottom of the
pane, not per-row. It carries audition status and error messages.

---

## InstrumentRow — collapsed strip

A `CTkFrame` whose visible content is a single horizontal strip:

| Column | Widget | Notes |
|--------|--------|-------|
| 0 | `CTkCheckBox` (no text) | toggling it auditions the checked set |
| 1 | `CTkEntry` | Name; placeholder "Name" |
| 2 | `CTkEntry` | Pitch; defaults to "C4" |
| 3 | `CTkSlider` 0–127 | Volume; default 100 |
| 4 | `CTkLabel` instrument name | chosen melodic abbreviation, or drum sub-instrument name for percussion; non-interactive display; weight=1 so it expands and pushes col 5 to the right edge; clicking it expands the row |
| 5 | `CTkLabel` "▾" | expand affordance; right-aligned |

Name, Pitch, and Volume live here and are **not** repeated in the expanded
panel. The Volume slider follows the labeled-slider pattern (label · slider ·
value readout). See `design/midi-values.md` for ranges and defaults.

**Click-to-expand:** `<Button-1>` is bound to the row frame, the strip frame,
the instrument-name label (col 4), and the "▾" toggle label (col 5). The
interactive widgets (checkbox, both entries, the Volume slider) keep their own
click semantics.

---

## InstrumentRow — expanded panel

An additional `CTkFrame` rendered directly below the collapsed strip. It is
hidden (`pack_forget`) when collapsed and shown (`pack`) when expanded.
Expansion toggles by clicking the non-interactive areas of the row; the
"▲ Collapse" button inside the expanded area collapses it.

The panel is the 5-line stack from `design/midi-values.md`. All lines are now
built:

```
Line 1 (per-note remainder):  [Vel] [Dur] [Instrument ▾]
─── Separator ──────────────────────────────────────────────────
Line 2 (channel basics):      [Pan] [Reverb] [Chorus]
Line 3 (2D curves):           [Expression ▾] [Brightness ▾] [Bend ▾]
Line 4 (Vibrato):             [Vibrato Depth] [Rate Hz] [Delay s]
Line 5 (ADSR):                [Attack s] [Decay s] [Sustain %] [Release s]
                                                          [ ▲ Collapse ]
```

Volume is in the collapsed strip, so Line 2 here holds only Pan / Reverb /
Chorus. Every slider auditions on mouse-up (`<ButtonRelease-1>`). Linear
sliders carry an integer readout; exponential sliders (Vibrato Rate/Delay,
Attack, Decay, Release) map slider position geometrically between the endpoints
documented in `design/midi-values.md`. CC numbers, ranges, and GM defaults are
specified there and are not duplicated here.

### Line 3 — 2D curve sliders (Expression / Brightness / Bend)

Each of the three widgets on Line 3 is a `ThumbnailWidget` from
`libs/slider_2d/`. Clicking a thumbnail opens an `EditPopup` that lets the user
draw a curve over the repeat interval. The popup bottom margin hosts a
**RepeatSlider** add-on (passed as the `add_ons` builder argument to
`ThumbnailWidget`/`EditPopup`).

**RepeatSlider** (`music/repeat_slider.py`) is an exponential slider spanning
0.05 s to 10 s for choosing the curve's repeat interval, with a
`get_repeat_seconds()` accessor; it fires `on_change` on mouse-up.

The `EditPopup` also has a **Reset** button. Pressing it restores the curve to
its flat default (via `Curve.reset` on the library side) and resets the
RepeatSlider to 1.0 s (wired in `instruments_pane.py` via the popup's
`on_reset` callback).

The curve data and the repeat value both **persist on the row** (stored on the
`InstrumentRow` instance and saved to `appdata/musicdata/instruments.json` on every
change). During audition, `instruments_pane.py` builds an `automation` list from
the three curves and their repeat values and passes it to
`MusicPlayer.audition(automation=...)`.

`MusicPlayer.audition` spawns a single daemon thread that ticks every ~10 ms.
On each tick it computes `phase = (elapsed % repeat_s) / repeat_s` and evaluates
the curve's monotone Hermite `sample(phase)` to get the current value, then
sends CC11 (Expression), CC74 (Brightness), or pitch-bend on the row's channel.
A per-channel generation counter allows preemption: a new audition call
increments the generation, and the old thread exits when it detects the mismatch.
Tail note-offs are sent after the note duration regardless of the thread's
lifecycle. The FLAC render path is untouched by the automation mechanism.

---

## Instrument grid popup

The "Instrument ▾" button on Line 1 opens `InstrumentGridPopup`, an
8-column × 17-row grid of GM instrument abbreviations. The 128 melodic cells
are displayed **alphabetically by full name, column-major**: entry index `i`
from `melodic_alphabetical()` is placed at `grid_row = i % 16`,
`grid_col = i // 16`, so column 1 holds the first 16 alphabetical instruments
top-to-bottom, column 2 the next 16, etc. Row 17 is the eight percussion kits
(shown with a distinct fill color). The popup is borderless and grabs focus.
All text in the popup uses fonts scaled ~40% larger via `_FONT_SCALE = 1.4`
and a `_scaled_font()` helper in `instruments_pane.py`.

- Picking a **melodic** cell sets the row's bank/program, updates the button
  label, unlocks the Pitch box (always editable for melodic; restores "C4"
  only when leaving percussion because the kit abbreviation is not a valid
  pitch), clears `_drum_note`, auditions — and **the popup stays open** so the
  user can audition several instruments in succession.
- Picking a **percussion kit** cell opens `PercussionSubGridPopup` alongside
  the main grid. Picking a drum sound in that sub-grid applies the sound and
  auditions — and **both popups stay open** so the user can try other kits or
  drums.
- Both popups close together only on `<Escape>` on either popup, or when focus
  genuinely leaves the popup group (detected via a deferred `after_idle` check
  on `<FocusOut>`). A pick alone does **not** close the popups.

### Single source of truth for Pitch state

`InstrumentRow` is the sole owner of pitch state:

- `self._pitch_var` (a `tk.StringVar` bound **only** to the strip's
  `_pitch_entry`) is the canonical pitch text.
- `self._is_percussion` (bool) is the canonical lock state.
- All pitch-text changes go through `_set_pitch_text(text)`, which writes
  `_pitch_var` and, if the popup is open, calls `popup.set_pitch_text(text)`.
- All lock changes go through `_apply_pitch_lock(locked)`, which configures
  the strip entry and, if the popup is open, calls `popup.set_pitch_locked(locked)`.
- Melodic selection always calls `_apply_pitch_lock(False)` — the box is
  never left disabled after a melodic pick.

`InstrumentGridPopup` owns its **own local** `tk.StringVar` for its Pitch
entry (created in `_build_pitch_header`, never shared with the row). This
decoupling eliminates the stale write-trace crash: when the popup Toplevel is
torn down by `close_all()`, the popup's StringVar is destroyed with it and the
row's `_pitch_var` is never touched. The popup reports Pitch edits to the row
via `on_pitch_change(text: str)`, wired to `InstrumentRow._on_popup_pitch_edit`.

---

## Dynamic channel allocation

`_ChannelAllocator` (a music-local private class, not a `libs/` module) hands
out MIDI channels 0..15. `acquire(row_id)` returns the channel already bound to
that row, or the lowest free channel, or `None` if all 16 are busy.
`release(row_id)` returns a channel to the free pool.

When a set of rows auditions, the pane acquires one channel per row and fires
the chord. It then schedules the matching **release** on the Tk main thread via
`after(duration_s + slop)`, so a channel only stays bound while it is sounding.
To avoid a stale release freeing a channel that a re-audition is still using,
the pane tracks one pending-release `after` id per row (`_pending_release`);
each fresh audition cancels the row's prior pending release before scheduling a
new one, and the release callback pops its own entry. Net effect: exactly one
outstanding release per row, timed from its most-recent audition. The allocator
dict and all `after` scheduling are touched only from the main thread.

The error-tone path uses channel 0 directly (not the allocator).

---

## Audition triggers

A row auditions itself plus every other currently-checked row when:

- its checkbox becomes checked (unchecking re-auditions just the remaining
  checked set),
- a slider is released after a drag (mouse-up),
- a text entry fires `<Return>`, `<Escape>` (loses focus; not a cancel), or
  `<FocusOut>`,
- an instrument grid pick is made (melodic or percussion).

Before firing, the set is validated; on success each row plays its chord on its
allocated channel with the row's CCs (volume, pan, reverb, chorus, vibrato
depth/rate/delay, attack, release) applied via `MusicPlayer.audition`.

**Vibrato rate (CC 76) and delay (CC 77)** are additionally sent as Roland GS
vibrato NRPNs (the NRPN MSB/LSB numbers live in `design/midi-values.md`) because
most GM SoundFonts ignore CC 76/77; the raw CCs are still sent too. Audibility
depends on the SoundFont honouring GS NRPNs.

---

## Percussion 7×7 sub-grid

`PercussionSubGridPopup` is a borderless `tk.Toplevel` that opens when the user
clicks a percussion kit cell in `InstrumentGridPopup`. It shows the 49-cell
padded grid (47 active drum sounds from `PERCUSSION_NOTES` + 2 blank/disabled
cells) laid out in 7 columns × 7 rows. Header shows the kit abbreviation and
full name.

**Placement and movability.** The sub-grid opens to the right of the grid popup,
but flips to the LEFT when a right placement would clip the right screen edge, and
clamps its y so it stays fully on-screen (pure helper
`clamp_subgrid_position(parent_x, parent_y, parent_width, sub_width, sub_height,
screen_width, screen_height)`). It remains a borderless, focus-grabbing
`overrideredirect` popup (so the focus-out close behavior below is preserved), and
its header strip doubles as a **drag handle** — `<ButtonPress-1>` records the
pointer-to-window offset and `<B1-Motion>` repositions the window — so the user can
move it anywhere.

Picking a drum sound:
- calls `InstrumentRow._on_pick_drum(kit_bank, kit_program, kit_abbr,
  kit_full, drum_name, drum_note)`,
- updates the Instrument button to show the drum name,
- locks the Pitch box to the kit abbreviation,
- stores `_drum_note` on the row,
- auditions the selected drum note,
- **keeps both popups open** so the user can try other drum sounds or kits.

Both popups close only on `<Escape>` or a genuine click outside the popup group
(focus leaving both popups), matching the melodic grid's close policy.

On a melodic pick after a percussion pick, `_drum_note` is reset to `None` and
the Pitch box is unlocked back to `"C4"`. If no drum sound has been chosen yet
(kit selected but sub-grid not opened), percussion auditions fall back to
Acoustic Snare (note 38).

---

## Error highlight behavior

When a note can't be played, the pane highlights the participating widgets in
red (until the next audition), writes a message to the window-level status bar,
and plays a C4–F#4–C4–F#4 error tone (0.125 s each) on channel 0. The
conditions and which widgets light up follow the error-highlight table in
`design/midi-values.md`:

- more than 16 instruments checked at once → every checked checkbox,
- two instruments share a name → both Name boxes,
- a row's Pitch box is empty or holds an illegal designator → that Pitch box.

---

## Persistence

Instrument rows are saved to and loaded from a single JSON file at
`<repo>/appdata/musicdata/instruments.json`.

### What is saved

`InstrumentRow.to_state_dict()` captures all instrument data needed to fully
reconstruct a row: name text, pitch text (raw string even when percussion-locked),
`is_percussion`, `percussion_kit_abbr`, `drum_note`, bank, program,
`instrument_label`, `instrument_full`, velocity, duration_s, volume, pan,
reverb, chorus, vibrato depth/rate_hz/delay_s, attack_s, decay_s, sustain_pct,
release_s, the three automation curves (via `libs.slider_2d.to_dict`), and the
three repeat floats.

**Not saved:** transient UI state — checked status, expanded/collapsed state.

### File format

```json
{ "version": 1, "rows": [ <state_dict>, ... ] }
```

Written atomically (temp file + `os.replace`) to prevent partial reads.

### Save-on-change

`InstrumentsPane.persist_instruments()` is called at the end of every
`_audition_set` (which fires on every control change) and after `_add_row`.
To suppress redundant disk writes (e.g. pure-audition checkbox toggles that
change no data), the method serializes all rows to JSON and compares it with
`_last_written_json`; if identical it returns without writing.  On an actual
write the new mtime is stored in `_last_file_mtime`.

### Load-on-startup

After building the UI, `InstrumentsPane.__init__` calls
`_load_saved_instruments()`, which reads the file via
`instruments_store.load_instruments()`.  For each saved dict it creates an
`InstrumentRow(state=...)`.  The `_last_written_json` is seeded from the loaded
rows so the first audition after startup does not write redundantly.

### Reload from disk

`InstrumentsPane.reload_from_disk()` checks whether the file mtime is newer
than `_last_file_mtime`; if so it destroys all current rows, rebuilds from the
file, and updates `_last_file_mtime` and `_last_written_json`.

### Cross-process sync

Two running copies of the music app stay in sync via mtime-gated reloads
triggered by focus events:

- `InstrumentsPane.sync_if_file_changed()` is a thin public wrapper around
  `reload_from_disk()` that adds a `_syncing` boolean reentrancy guard
  (set/cleared in a `try/finally`) so a reload that triggers child focus events
  cannot recurse.
- `<FocusIn>` and `<FocusOut>` are bound on **the pane window itself** (inside
  `InstrumentsPane.__init__`) so focus entering or leaving the instruments
  window checks for an external write.
- `<FocusIn>` and `<FocusOut>` are also bound on **the main application window**
  (via `MusicApp._bind_focus_sync()`, called at the end of `__init__`) so focus
  changes on the main editor window also trigger a check.  The handler looks up
  `self._instruments_pane` at call time rather than capturing it at bind time, so
  it always reaches the current pane instance even if `_show_instruments` ever
  recreates it.
- **Last-writer-wins:** when this process writes the file, it stores the new
  mtime in `_last_file_mtime`, so its own next focus event sees the same mtime
  and returns immediately — our own writes never trigger a spurious reload.
- If the pane is withdrawn (not visible), the reload still rebuilds the
  in-memory rows (harmless); they display correctly next time the pane is
  deiconified.  The sync path never deiconifies the pane.

### Lifecycle: create-at-startup withdrawn

`MusicApp.__init__` calls `_create_instruments_pane()` immediately after
`_build_ui()`, which creates and `withdraw()`s the pane before any UI event
loop starts.  This means saved instruments are loaded at startup without
flashing a visible window.

---

## Files

| File | Change |
|------|--------|
| `music/instruments_pane.py` | `InstrumentsPane`, `InstrumentRow`, `InstrumentGridPopup`, `PercussionSubGridPopup`, `_ChannelAllocator`. Phase 2: added `PercussionSubGridPopup`; row-17 kit clicks open sub-grid; Line 3 `ThumbnailWidget` trio (Expression/Brightness/Bend) with `RepeatSlider` add-on; 3 curves + 3 repeat floats persist on row; audition builds `automation` list; extracted shared exponential helper to `exponential_slider.py`. Phase 3: `InstrumentRow.__init__` accepts `state=`; `to_state_dict()`; `InstrumentsPane` gains `persist_instruments()`, `reload_from_disk()`, `_load_saved_instruments()`. |
| `music/instruments_store.py` | **New (phase 3).** `instruments_path()`, `file_mtime()`, `save_instruments()`, `load_instruments()` — pure file-I/O around `appdata/musicdata/instruments.json`. |
| `music/pitch.py` | **New.** `parse_pitch_chord` + `PitchParseError` — note-designator parsing. |
| `music/gm_instruments.py` | **New.** `MELODIC_GRID`, `PERCUSSION_KITS`, `PERCUSSION_NOTES` (47 entries, notes 35–81), `melodic_program_for`, `percussion_kit_for`, `percussion_notes_padded_for_grid` (pads to 49 cells for a 7×7 grid). |
| `music/music_player.py` | Gained `audition(...)` — synchronous one-chord playback with CCs and pitch-bend, note-offs on a daemon Timer. Phase 2: `automation=` parameter accepts list of cc/bend curve dicts; spawns ONE daemon thread that sends curves over time (~10 ms tick, `phase=(elapsed%repeat_s)/repeat_s`); per-channel generation dict for preemption; tail note-offs; FLAC path untouched. |
| `music/exponential_slider.py` | **New (phase 2).** Shared `slider_value_to_exponential` / `slider_position_for_exponential` helpers, extracted from `instruments_pane.py`. |
| `music/repeat_slider.py` | **New (phase 2).** `RepeatSlider` widget — exponential 0.05→10 s, `get_repeat_seconds()`, `initial_value`, `on_change` on mouse-up. |
| `music/music.py` | Add `Instruments` button; hold `_instruments_pane`; add `_show_instruments` handler. Phase 3: pane created at startup withdrawn (`_create_instruments_pane`); `_show_instruments` deiconifies the always-present pane. |
| `music/README.md` | `Instruments` row in the UI button table. |
| `libs/slider_2d/curve.py` | Phase 2: `sample` is real (monotone Hermite interpolation). |
| `libs/slider_2d/widgets.py` | Phase 2: `ThumbnailWidget` and `EditPopup` accept an `add_ons` builder packed in the popup bottom margin. |
| `design/music_app.md` | Channel-assignment notes. |

