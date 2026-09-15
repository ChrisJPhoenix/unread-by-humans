# tempo_map

Converts beat positions to wall-clock seconds for the music parser, given a
tempo map that can change BPM at arbitrary beat positions.

## Segment model

A tempo map is an ordered list of `(beat, bpm)` segments. Each segment runs
at its BPM until the next segment begins; the last segment extends to the end
of the song. One quarter-note beat lasts `60 / bpm` seconds.

`build_tempo_map` always returns a map whose first segment starts at beat 0.
When no input change sits exactly at beat 0, a default `(0.0, 120.0)` segment
is prepended automatically.

## Conflict rule

If two input rows specify **different** BPM values at the same beat position,
`build_tempo_map` raises `TempoConflictError`. Two rows with the **same** BPM
at the same beat are silently collapsed into one segment.

## Public API

| Function | Description |
|---|---|
| `build_tempo_map(changes)` | Build a sorted `[(beat, bpm), ...]` map from raw `(beat, bpm)` change events. |
| `beats_to_seconds(beat, tempo_map)` | Seconds elapsed from beat 0 to `beat` under the given map. |
| `segment_seconds(start_beat, end_beat, tempo_map)` | Seconds elapsed between two beat positions (handles tempo changes mid-note). |

## Test plans

| File | Purpose |
|---|---|
| `test_primary.txt` | Primary plan covering the full API — all 7 stanzas run in both `--quick` and `--full` modes. |
