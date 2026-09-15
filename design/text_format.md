# Music Text Format

The library implementing this format is `libs/music_parser/`.

## Document structure

Sections are separated by blank lines. `#` lines are comments.

```
# comment

intro
piano:g3 4/4 g g a- g- c- b---
guitar:e2 4/4 . . c- . g- .

verse
include intro
violin:d4 3/4 d e f | g a b |

song
include intro verse verse
```

## Section definition

First line of a section: a bare name (no spaces, no colons; letters, digits, underscore, dot, and hyphen are allowed — e.g. `verse1.1`, `verse2-l2`).
The **topmost section** in the file may omit the name line entirely; its content
begins on the first non-blank, non-comment line.
Remaining lines: instrument lines or include lines, in any order.
All lines in a section play **in parallel** (simultaneously).

## Instrument lines

```
instrument:anchor [token ...]
```

**Instruments:** `piano`, `guitar`, `guitar-electric`, `sitar`, `violin`, `organ-reed`

**Anchor:** note letter + octave digit, e.g. `g3`, `c4`. Sets the default octave
for unqualified note names on this line (not sticky to other lines).

Default octave rule: if note letter ≥ anchor letter (in C-major order C<D<E<F<G<A<B),
use anchor octave; otherwise use anchor octave + 1.

Example: anchor `g3` → g=3, a=3, b=3, c=4, d=4, e=4, f=4

### Tokens (instrument lines)

**Defaults at start of line: `v80 4/4`.**

| Token | Meaning |
|---|---|
| `vN` | Set velocity to N/100 × 127. N must be 1–100. `v` and `V` are equivalent. |
| `N/D` | Time signature: N beats per measure, increment = 1/D note. |
| `bpm=N` | Set tempo to N beats per minute from this position onward. Case-insensitive `bpm`; N must be positive. Affects all parallel tracks and sections included after this point. |
| `\|` | Measure bar. First measure may be fractional (pickup). Subsequent measures are validated against beats_per_measure. |
| `.` | Rest. A bare `.` is one increment. `.N` is N increments (e.g. `.5`). `.` plus extra dots or dashes is `1 + count` increments (e.g. `.....` and `.----` both equal five). |
| `_` | Rest to end of current measure. |
| `_N` | N full measures of silence (N × beats_per_measure increments). |
| note | See note syntax below. |

**Modal settings** (`vN`, `N/D`) take effect at their position and persist to the
right until the next setting of the same kind or end of line.

### Note syntax

```
letter [octave-digit | octave-markers] [accidental] [dashes]
```

- **letter**: `a`–`g`, case-insensitive. The only case-sensitive distinction in the
  entire language is `b` (flat accidental, lowercase only) vs `B` (note name, never
  a flat). All other tokens — including velocity (`v`/`V`) — are case-insensitive.
- **octave**: two mutually exclusive forms, both optional:
  - **digit** (`0`–`9`): overrides the default octave for this note only.
  - **octave markers** (`^` up, `v`/`V` down): one or more markers shift the note's
    default octave (the same per-letter default-octave rule used for unqualified
    notes). Formula: `octave = default_octave + (count of ^) − (count of v/V)`.
    Markers stack: `g^^` = +2 octaves, `gvv` = −2 octaves. `v` and `V` are
    equivalent (only `b`/`B` is case-sensitive in this language).
    Worked examples — anchor `g4` (where `g` defaults to octave 4 and `d` to 5):
    `g^` = g5, `dv` = d4, `dvv` = d3. Anchor `g5`: `g^` = g6, `gv` = g4.
    Markers combine freely with accidentals and dashes: `g^#`, `g^-`.
- **accidental**: `#` (sharp) or `b` (flat, lowercase only). Comes after octave if present.
- **dashes**: each `-` adds one increment to duration. `c` = 1 increment, `c-` = 2, `c--` = 3.

Examples: `c`, `C`, `g4`, `c#`, `c4#`, `bb`, `b4b`, `c--`, `g^`, `dv`, `g^^`, `g^#`, `g^-`

### Duration and timing

- Default tempo is 120 BPM (quarter note = 0.5 s). A `bpm=N` token sets the tempo from its beat
  position onward across all parallel tracks and later included sections, until the next `bpm=N`
  token. A standalone `bpm=N` line at the top of the file applies from the song start.
- Increment duration = `(4 / D) / bpm × 60` seconds for time signature `N/D` at the current tempo.
- `6/8` increment = 1/8 note = 0.25 s at 120 BPM. One 6/8 measure = same wall-clock time as one 3/4 measure.

## Percussion lines

When an instrument is a **percussion** instrument — i.e. the designed instrument
selected in the Instruments pane is a drum sound (`is_percussion`, with a
`drum_note`) — its line uses an anchor-free drum syntax instead of pitched notes:

```
InstrumentName [token ...]
```

- **No `:anchor`.** A percussion line is just the instrument name followed by
  tokens; there is no base note. (A line whose name is a *melodic* instrument
  written without its `:anchor` is an error — see Error cases.)
- The only "note" token is **`z`** (case-insensitive `z`/`Z`): a single hit of the
  instrument's selected drum sound. The hit sounds `drum_note` on the
  instrument's channel; there is no pitch, octave, or accidental on a percussion
  line (write `z`, not a letter).
- A `z` consumes trailing **dashes** to sustain (ring undamped): `z` is one
  increment, `z-` two, `z--` three — same dash semantics as a pitched note.
- Rests use `.` exactly as on melodic lines (`.` = one increment, `...` / `.----`
  = three / five, `.N` = N).
- **Whitespace is optional between drum tokens.** Because `z` is one unambiguous
  character, a whitespace-delimited run composed only of `z`/`.`/`-` is decoded
  left-to-right into hits and rests: `zzzz` is four hits, and `z...z--.` is hit,
  rest(3), hit(3), rest(1). Structural tokens (`vN`, `N/D`, `bpm=`, `|`, `_`,
  `_N`) still apply exactly as on melodic lines and must remain whitespace-separated.

Example (8/8, increment = 1/2 beat): `CowBell 8/8 z...z--. | z- . . zzzz` rings the
cowbell for 1/2 beat, rests 3 increments, rings (undamped) for 3 increments,
rests 1, bars, rings for 2 increments, rests twice, then four 1/2-beat hits.

## Include lines

```
include [token ...]
```

An include line is one parallel track. Tokens are processed left to right as a
sequential timeline: section names play one after another, with silence and
control codes interspersed.

### Tokens (include lines)

**Defaults at start of line: `v100 4/4`.**

| Token | Meaning |
|---|---|
| `SectionName` | Play that section sequentially at this point. Accepts the same characters as section-header names: letters, digits, underscore, dot, and hyphen (e.g. `verse1.1`, `verse2-l2`). |
| `vN` | Multiply every NoteEvent velocity in sections to the right by N/100. Values > 1 are allowed (e.g. `v150`) provided no resulting velocity exceeds 100 on the 0–100 scale; otherwise a parse error is reported. |
| `N/D` | Time signature for computing subsequent `_N` durations. |
| `bpm=N` | Set tempo to N beats per minute from this position onward. Case-insensitive `bpm`; N must be positive. Also propagates bpm changes from any included section into the including section's timeline. |
| `\|` | Measure bar (optional; validated if present). |
| `.` | Rest. A bare `.` is one increment. `.N` is N increments. `.` plus extra dots or dashes is `1 + count` increments (e.g. `.....` and `.----` both equal five). |
| `_` | Rest to end of current measure. |
| `_N` | N full measures of silence at the current time signature. |

**Modal settings** (`vN`, `N/D`) take effect at their position and persist to the
right until the next setting of the same kind or end of line.

Beat-count labels (`[N]`) are injected by the program immediately after each
section name with no space, on both header lines and include references.
These are non-editable embedded widgets.

## Beat counts

- Displayed as an integer if the value is a whole number, otherwise float to 2 dp.
- Section beat count = max duration of all its parallel tracks, in quarter-note beats.
- Beat count of an include sequence = sum of each section's beat count + silence durations.

These values are what the editor pane's "Beat-count labels" (`[N]` embedded
widgets) display; that design doc stays in the private monorepo this app was
carved out of.

## Examples

### Happy Birthday
```
happy_birthday
piano:g3 v50 4/4 g g a- g- c- b--- | g g a- g- d- c--- | g g g4- e- c- b- a- | f f e- c- d- c---
```

### Guitar riff gap
```
song
include verse verse chorus | 4/4 _16 | chorus
guitar-electric:e2 v80 4/4 [riff notes here]
```
The include line plays verse+verse+chorus, 16 bars silence, chorus.
The guitar riff plays in parallel across the entire duration.

## Error cases

- Unknown instrument name
- A melodic instrument line written without its `:anchor` (e.g. `piano c d`); only
  percussion instruments may omit the anchor
- Bad anchor (not letter+digit)
- Unrecognized token
- Measure beat count mismatch (after first bar)
- Section include cycle (A→B→A)
- Unknown section name referenced in an include line
- `vN` on an instrument line with N = 0 (MIDI note-on with velocity 0 is treated as
  note-off by some synths, making behaviour inconsistent; use a low value like `v1` instead)
- `vN` on an instrument line with N > 100
- Include-line `vN` multiply result > 100 for any note in the included section
- More than 16 instrument channels
- `bpm=N` with N ≤ 0
- Two different `bpm` values at the same beat position (tempo conflict)

The editor pane's "Error highlighting (future)" section is the UI that will
surface these as tagged error tokens; that design doc stays in the private
monorepo this app was carved out of.
