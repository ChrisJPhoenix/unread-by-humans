# Midi Values: designing notes and instruments

This file describes:
 - the contents of the expanded panel of an `InstrumentRow`,
 - the layout of that expanded panel,
 - the instrument grid that opens from the "Instrument" dropdown, and
 - the percussion sub-grid for picking a drum sound within a kit.

The compressed (collapsed) panel contains the checkbox, user-assigned
instrument name, pitches, and channel volume.

## Purpose

Let the user hear the result of various MIDI parameters and configure
instruments for use in the program's output.

## Approach

A panel of controls for every value MIDI uses for instruments and notes.
Every change plays the note so the user can hear the result. When the user
is happy, an instrument exists (values to program the channel, defaults for
per-note values) that can be used in the song-specification.

## Controls

Sliders all have 64 pixel range, with extreme values labeled at the ends.
Play on mouse-up that changed the slider.

Text boxes are one-line, 10 chars unless otherwise specified. Play on
`<enter>`, `<esc>` (which simply loses focus, it's not a cancel), or loss
of focus.

Every 2D slider in this pane is **per-channel** — its curve is sent
continuously to the channel during playback. Every 2D slider ships with a
**repeat slider** add-on. The starting curve is flat at the control's GM
default. The curve's two endpoints (x=0 and x=1) move together in value —
dragging either sets both — so the baseline stays level (the 2D-slider design
doc stays in the private monorepo this app was carved out of).

The "repeat slider" widget is a slider with a special feature.
 - The slider displays `f"Repeat every {value} seconds"`
   where `value` changes exponentially from 0.05 at the leftmost pixel to
   10 at the rightmost pixel. Format from "0.05" to "0.06" to "0.11" to
   "0.99", then "1.0" to "9.9", then "10.0".

### Checkbox

Whenever an instrument's value is modified, its chord should be played,
interrupting any currently playing note. Any other instrument with the
checkbox checked should also be played.

### Instrument name

A text box in which the user can type a name (no spaces) to be used at the
start of a line in the song file. If more than one instrument has the same
name, the box should be highlighted in red, and trying to play or output
the song file should put up an error dialog instead of playing. Label:
`"Name"`.

### Pitches

A text box in which the user can type note designators: C3, D#4, etc.
Multiple notes can be typed to make a chord. Initial value is `"C4"`. Any
separators (hyphen, comma, space) should be allowed. Empty box is an
error. Label: `"Pitch"`.

**When a percussion kit is selected**, this box becomes non-editable and
displays the kit abbreviation (`P.Std`, `P.Room`, `P.Power`, `P.Elec`,
`P.808`, `P.Jazz`, `P.Brush`, `P.Orch`). The actual drum note is determined
by the sub-instrument chosen in the second-level popup, not by user pitch
entry.

### Velocity

A slider 0-127 labeled `"Vel"`. GM default 100.

### Duration

A slider 0-4 (seconds) labeled `"Dur"`. GM default 1.0.

### Channel

Not exposed in the UI. Channels are assigned dynamically at play time:
when an instrument needs to sound, it is bound to the lowest free MIDI
channel for the duration of its activity, then released. There is no
hardwired percussion channel — percussion is achieved by selecting a
percussion-kit entry in the Instrument grid, which sets the bound
channel's bank/program internally.

The only related illegal condition is attempting to play more than 16
instruments at once (see the error-highlight table).

### Instrument

A dropdown that opens an 8-column × 17-row grid of instrument names so
all are visible at once. The first 16 rows are the 128 GM melodic
programs grouped by family; the 17th row holds 8 percussion kits.
Clicking a percussion-kit cell opens a second-level popup with the
drum sounds for that kit. See "Instrument grid" and "Percussion
sub-grid" below. Label: none.

### Bank

Not exposed in the UI. For non-percussion instruments, bank-switched
variations that behave the same across software synths simply appear as
additional entries in the instrument grid. For percussion, each of the
8 kits corresponds to a specific bank/program pair and is handled
internally.

### Separator

A horizontal line separating the per-note values above from the
per-channel values below.

### Volume

Slider 0-127. Label: `"Volume"`. GM default 100.

### Pan

Slider 0-127. Label: `"Pan"`. GM default 64 (center).

### Reverb

Slider 0-127. Label: `"Reverb"`. GM default 40. (CC 91.)

### Chorus

Slider 0-127. Label: `"Chorus"`. GM default 0. (CC 93.)

### Expression

2D slider 0-127. Label: `"Expression"`. GM default 127 (flat curve at
top). (CC 11.)

### Brightness

2D slider 0-127. Label: `"Brightness"`. GM default 64. (CC 74.)

### Bend

2D slider, -6 to +6 semitones. Label: `"Bend"`. GM default 0 (flat curve
at center). This is MIDI pitch-bend, scaled to ±6 semitones via the
standard pitch-bend-range RPN (set once when the channel is configured).

### Vibrato

These should be on a horizontal line, left to right.

#### Depth

Slider 0-127. Label: `"Vibrato Depth"`. GM default 0. **CC 1** (modulation
wheel).

#### Rate

Slider, exponential 100 at left, 0.1 at right. Label: `"Rate Hz"`. GM
default mid. **CC 76**. Also sent as Roland GS vibrato NRPN (MSB 0x01,
LSB 0x08) because most GM SoundFonts ignore CC 76. Audibility depends on
the SoundFont honouring GS NRPNs.

#### Delay

Slider, exponential 0.01 - 10. Label: `"Delay s"`. GM default leftmost
(no delay). **CC 77**. Also sent as Roland GS vibrato NRPN (MSB 0x01,
LSB 0x0A) because most GM SoundFonts ignore CC 77. Audibility depends on
the SoundFont honouring GS NRPNs.

### ADSR (all on one line at the bottom)

Envelope (not always supported by SoundFonts).

#### Attack

Slider, exponential 0.001 - 2. Label: `"Attack s"`. **CC 73** — time to
reach full volume after note-on.

#### Decay

Slider, exponential 0.01 - 4. Label: `"Decay s"`. Sent as SF2/NRPN where
supported.

#### Sustain

Slider, linear 0-100. Label: `"Sustain %"`. SF2/NRPN sustain level.

#### Release

Slider, exponential 0.001 - 2. Label: `"Release s"`. **CC 72** — time to
fade after note-off.

---

## Expanded panel layout

The expanded panel appears below the collapsed strip of an
`InstrumentRow`. Controls are arranged as a stack of horizontal lines —
no multi-column split:

```
Line 1 (per-note remainder):  [Vel] [Dur] [Instrument ▾]
─── Separator ──────────────────────────────────────────────────
Line 2 (channel basics):      [Volume] [Pan] [Reverb] [Chorus]
Line 3 (2D sliders):          [Expression ▱] [Brightness ▱] [Bend ▱]
Line 4 (Vibrato):             [Vibrato Depth] [Rate Hz] [Delay s]
Line 5 (ADSR):                [Attack s] [Decay s] [Sustain %] [Release s]
```

Name, Pitch, and Volume already live in the collapsed strip and are not
repeated here. Each `▱` is a `slider_2d.ThumbnailWidget`; clicking it
opens an `EditPopup` with the repeat-slider in the add-on margin.

The **status bar** is window-level: a single bar at the bottom of the
`InstrumentsPane`, not per-row.

---

## Instrument grid

Clicking the "Instrument" dropdown opens a popup window containing an
8-column × 17-row grid. Each cell holds a label up to 10 characters in a
mono font; the full GM name appears as a tooltip on hover. Row 17 is the eight
percussion kits.

> **Display order note:** The table below lists the 128 GM programs grouped by
> family — this is the canonical program↔name reference. The popup itself
> **displays the 128 melodic instruments alphabetically by full name,
> column-major** (entry index `i` at `grid_row = i % 16`, `grid_col = i // 16`,
> so the first column top-to-bottom is the first 16 alphabetical names). The
> program number and bank for each instrument follow the instrument's identity,
> not its grid position. Percussion kits remain in row 17.

| Row | Family | Col 1 | Col 2 | Col 3 | Col 4 | Col 5 | Col 6 | Col 7 | Col 8 |
|----:|--------|-------|-------|-------|-------|-------|-------|-------|-------|
| 1  | Piano          | Ac.Grand | Br.Acoust | El.Grand | Hnk-tonk | ElPiano1 | ElPiano2 | Hrpschrd | Clavinet |
| 2  | Chromat.Perc   | Celesta  | Glocken   | MusicBox | Vibrphn  | Marimba  | Xylophn  | TubBells | Dulcimer |
| 3  | Organ          | Drawbar  | PercOrg   | RockOrg  | ChrchOrg | ReedOrg  | Accordion| Harmonica| TangoAcc |
| 4  | Guitar         | Nylon Gt | Steel Gt  | Jazz Gt  | Clean Gt | Muted Gt | Ovrdr Gt | Dist Gt  | GtrHarmn |
| 5  | Bass           | AcsticBs | FingerBs  | PickedBs | FretlsBs | SlapBs 1 | SlapBs 2 | SynBass1 | SynBass2 |
| 6  | Strings        | Violin   | Viola     | Cello    | Contrabs | TremStrg | PizzStrg | OrchHarp | Timpani  |
| 7  | Ensemble       | StrEns 1 | StrEns 2  | SynStr 1 | SynStr 2 | ChoirAah | VoiceOoh | SynVoice | OrchHit  |
| 8  | Brass          | Trumpet  | Trombone  | Tuba     | MuteTrp  | FrenchHn | BrassSec | SynBrs 1 | SynBrs 2 |
| 9  | Reed           | SopSax   | AltoSax   | TenorSax | BariSax  | Oboe     | EngHorn  | Bassoon  | Clarinet |
| 10 | Pipe           | Piccolo  | Flute     | Recorder | PanFlute | Bottle   | Shakuhci | Whistle  | Ocarina  |
| 11 | Synth Lead     | Square   | Sawtooth  | Calliope | Chiff    | Charang  | Voice    | Fifths   | Bass+Ld  |
| 12 | Synth Pad      | NewAge   | Warm      | Polysyn  | Choir    | Bowed    | Metallic | Halo     | Sweep    |
| 13 | Synth FX       | Rain     | Soundtrk  | Crystal  | Atmosphr | Bright   | Goblins  | Echoes   | Sci-Fi   |
| 14 | Ethnic         | Sitar    | Banjo     | Shamisen | Koto     | Kalimba  | Bagpipe  | Fiddle   | Shanai   |
| 15 | Percussive     | TinklBel | Agogo     | SteelDrm | WoodBlk  | Taiko    | MelodTom | SynthDrm | RevCym   |
| 16 | Sound FX       | GtrFret  | Breath    | Seashore | Birds    | Phone    | Helicptr | Applause | Gunshot  |
| 17 | Percussion     | P.Std    | P.Room    | P.Power  | P.Elec   | P.808    | P.Jazz   | P.Brush  | P.Orch   |

**Click behavior** — audition-on-pick, the grid staying open for several
auditions, closing only on `<Escape>` / focus leaving the popup group, and a
row-17 cell opening the **Percussion sub-grid** alongside the main grid — is UI
behavior owned by `design/instruments.md` ("Instrument grid popup").

---

## Percussion sub-grid

Opened by clicking any cell in row 17 of the instrument grid. A 7-column
× 7-row grid (47 cells used, 2 blank) of the GM percussion sounds for
the chosen kit (notes 35–81):

| Row | Col 1     | Col 2     | Col 3     | Col 4     | Col 5     | Col 6     | Col 7     |
|----:|-----------|-----------|-----------|-----------|-----------|-----------|-----------|
| 1 | AcsBassDr  | BassDrum1 | SideStick | AcsSnare  | HandClap  | ElSnare   | LowFlrTom |
| 2 | ClsdHiHat  | HiFlrTom  | PdlHiHat  | LowTom    | OpnHiHat  | LowMidTm  | HiMidTm   |
| 3 | CrshCym1   | HighTom   | RideCym1  | ChnsCym   | RideBell  | Tambrne   | SplshCym  |
| 4 | Cowbell    | CrshCym2  | Vibraslp  | RideCym2  | HiBongo   | LowBongo  | MuteHiCg  |
| 5 | OpnHiCg    | LowConga  | HiTimbal  | LowTimbl  | HiAgogo   | LowAgogo  | Cabasa    |
| 6 | Maracas    | ShrtWhis  | LongWhis  | ShrtGuir  | LongGuir  | Claves    | HiWoodBk  |
| 7 | LoWoodBk   | MuteCuca  | OpnCuica  | MuteTri   | OpenTri   |           |           |

**Selecting a cell** — writes the drum-sound name into the row's "Instrument"
display, locks the kit abbreviation into the Pitch box, records the kit's
bank/program + drum note number internally, auditions the selected drum, and
keeps both popups open (close only on `<Escape>` / focus leaving the popup
group) — is UI behavior owned by `design/instruments.md`
("Percussion 7×7 sub-grid").

---

## Defaults on **Add**

When the user clicks **Add**, the new row is populated with GM defaults:

| Control                  | Default |
|--------------------------|---------|
| Name                     | empty (user must fill in) |
| Pitch                    | C4 |
| Velocity                 | 100 |
| Duration                 | 1.0 s |
| Instrument               | Acoustic Grand Piano (row 1, col 1) |
| Volume                   | 100 |
| Pan                      | 64 |
| Reverb                   | 40 |
| Chorus                   | 0 |
| Expression               | flat at 127 |
| Brightness               | flat at 64 |
| Bend                     | flat at 0 semitones |
| Vibrato Depth            | 0 |
| Vibrato Rate             | mid |
| Vibrato Delay            | 0 (leftmost) |
| Attack                   | 0.01 s |
| Decay                    | 0.1 s |
| Sustain                  | 100 % |
| Release                  | 0.1 s |
| Repeat (all 2D sliders)  | 1.0 s |

---

## Semantics and Behavior

The user-entered name of the instrument will be used in the song-format
file.

Notes can have individual velocity and duration. We apply the same
velocity and duration to all notes in the chord.

Values from 2D slider curves will be sent to the synthesizer or MIDI
file every 0.01 seconds or faster. The Pulses Per Quarter Note should
be chosen so that a tick is ≤ 10 ms.

The "repeat slider" tells how much time it will take to send the entire
curve. When outputting a song, the curve should be sent continuously to
the channel. When playing a note in the Orchestra page, the curve should
start when the note starts.

The note(s) should start playing whenever a value is changed — e.g.
mouse-up on a slider or focus loss on a text box.

If it's impossible to play a note when you otherwise would, play a
quick C4-F#4-C4-F#4 sequence (0.125 sec each) and describe the error
in the window-level status bar at the bottom of the InstrumentsPane.
Highlight any entry widgets participating in the error in red until the
next note is played (or attempted).

### Error highlight table (partial; extend as new conditions appear)

| Condition                                                | Widgets highlighted             |
|----------------------------------------------------------|---------------------------------|
| More than 16 instruments checked at once                 | Every checked row's checkbox    |
| Two instruments share a name                             | Both rows' Name box             |
| Pitch box contains an illegal note designator            | This row's Pitch box            |
| Pitch box is empty                                       | This row's Pitch box            |
