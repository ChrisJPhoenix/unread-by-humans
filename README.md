# Music Workbench

> Experiment: from concept conversation to tested, working code with no human reading
> the design docs, plans, or code.

## The protocol

- A human and a model have a **concept conversation**.
- A model writes the **design docs** from that conversation.
- A model decomposes a design into **work-plan steps**.
- A model executes one fully-specified step; every step is gated by `./bin/green` —
  the test suite, not a human's reading.
- **No human reads the design docs, the plans, or the code.** The human reads the
  conversation, the gate output, and the running app.

What the human *does* do is the part that is easy to understate: he steers in
conversation, makes the scope and priority calls, runs the thing, and owns the
gates. A design doc in this repository containing a
line like "decision with Chris, 2026-07-16" is not evidence of a human having read
that doc — it is the model recording, in the artifact, a decision that was made in
conversation, not in review of the text. That is a stronger claim than "the human
reviewed nothing," not a weaker one, and it is why the human's remaining job is to
run the artifact rather than read it.

This does not claim the code is bug-free or that the tests are exhaustive. It
claims that every change here passed the same mechanical gate before it landed.

## 1. The experiment, and the machinery that makes it survivable

The loop, repeated once per unit of work: a design doc, then a plan step, then one
agent per step, then `./bin/green`, then a commit on green. `./bin/green` reports
the diff since the last verified checkpoint, the `./bin/dev test` return code,
and — on a non-zero return code — the test output, so an agent (or a human) never
has to trust its own account of what it did.

What each gate is *for*:

- **The plan-file harness.** Every library's behavior is pinned by a
  human-readable `test_*.txt` plan next to it, not by test *code* — a `call:`,
  some `arg`/`expect` lines, nothing a reader needs a Python interpreter to
  parse. `design/testing.md` is the grammar.
- **The stub contract.** A new function starts `@stub`-decorated, returning
  plausible dummy data, with a required `test_stub_<fn>.txt` it must pass as-is.
  An orphan in either direction — a stub with no plan, or a plan naming no real
  stub — exits 4. `libs/demo/` is the worked example (`future_thing`, its
  `test_stub_future_thing.txt`).
- **The three domains.** Domain 1 runs the Python plan files against `libs/`.
  Domain 2 runs Flask smoke tests through the test client — no real socket is
  ever opened. Domain 3 runs `node:test` unit tests with zero npm dependencies
  (no `package.json`, no `node_modules`).
- **The hang-detection ceiling.** `libs/dev_test/timeout_seconds.txt` is a
  committed, per-mode timeout that only ever rises. A run that trips it exits 5;
  a ceiling file that's missing or malformed exits 6 — a silent fallback would
  hide which ceiling is actually in force, which is worse than failing loudly.
- **The gate lock.** `bin/gate_lock.sh` takes an atomic `mkdir` lock per
  worktree and refuses rather than blocks (exit 7) when it can't. Without it,
  two concurrent gate runs race on the git index (`bin/green` commits a
  checkpoint), on `tmp/<prog>_test_last.txt`, on the timeout-ceiling file itself,
  and on the gated-live-test cache files under `tmp/`.
- **Untested-by-design boundaries.** `FluidSynthBackend` (the real synth glue),
  the `/music/flac` route, and `web/static/music_app.js` together with the
  browser DOM/AudioContext layer are not covered by the gate, and the repository
  says so rather than pretending otherwise. They need a native FluidSynth
  library, a soundfont, and a real browser — none of which the automated gate
  has.

**The sharpest illustration of both what this buys and where it fails** is in
`design/music-web.md`'s dated correction log. An earlier port of this app was
declared feature-complete and passed its entire suite, while every instrument
silently played as channel-0 Acoustic Grand Piano regardless of what was
selected — because the render pipeline hardcoded channel 0 behind the very seam
(`FakeBackend`) that let the rest of the pipeline be tested without a real
synthesizer, and a fake synth is silent by construction, so nothing in the gate
could have caught it. It was caught by a human *listening*, not by a human
reading code or diffs. That is exactly why the human's job here is to run the
artifact, not read it: the failure was invisible in the diff and obvious in one
second of audio.

The planner/worker agent definitions that dispatch and execute steps live in
tooling outside this repository. What ships here is the part that has to be
right regardless of which tooling drives it: the step discipline in
`design/work-plans.md`, and the gates above.

## 2. Run the music app

```
python3 -m venv venv
venv/bin/pip install -r requirements.txt
./bin/dev test
./bin/serve
```

then open `http://127.0.0.1:5050/music`.

Requirements: Python 3.11.2 (the version this project was built and gated
against), Node >= 18 for the Domain 3 test tier.

**You get a working editor and a green test suite with no audio dependencies at
all.** To actually *hear* anything you additionally need `pyfluidsynth`,
`libfluidsynth`, `python-soundfile`, and a soundfont at
`/usr/share/sounds/sf2/MuseScore_General_Full.sf2`. This boundary is deliberate
and documented — see `design/music-web.md`.

## 3. Architecture

- **WYHIWYG — one synthesizer, one playback path.** The instrument you audition
  in the editor is guaranteed identical to what plays in the final render,
  because there is exactly one synthesizer (an offline FluidSynth render to
  float PCM) and one playback path (raw float32 PCM through Web Audio). There is
  no second synthesis path for the two to diverge across.
- **The `FakeBackend` seam.** It buys full Domain-1 coverage of channel
  routing, MIDI programs, CCs, pitch bend, and the whole render schedule
  without needing FluidSynth installed. It does not buy knowing whether the
  result is audible — that is exactly the gap the correction log above is
  about.
- **Pure-core / thin-glue split.** `libs/` holds the logic (parsing, pitch,
  scheduling, the instruments model); `web/apps/*.py` holds none of it — the
  routes parse a request, call into `libs/`, and return the result.
- **The Python→JS constants bridge** (`design/web-runtime.md`) single-sources
  values that need to exist identically in both worlds, served as an ES module
  at `/<app>/js_constants.mjs` from a whitelist of Python constants.
- **`bin/look`** is a side-effect-free query CLI (definitions, references,
  outlines, pattern search, grep, git read-only, and a small shell-loop-
  replacement DSL) so an agent's read access can be allowlisted as one command
  instead of an open-ended shell.
- **Lineage.** This web app is a Claude-authored port of an earlier tkinter
  desktop app. The tkinter code itself is not in this repository, but its
  design doc is (`design/music_app.md`), and the score-text libraries
  (`libs/music_parser`, `libs/pitch`) are shared by both.

## 4. Provenance

This repository was carved out of a larger private monorepo. The music web app
and the dev test harness that gates it are complete and green here. Left
behind, deliberately: the tkinter desktop front-end this app was ported from,
the multi-agent orchestration machinery that drove the plan-and-dispatch loop
described above, and eleven other apps that lived in the same monorepo. The git
history in this repository starts at the carve-out — it is not the history of
the original work.
