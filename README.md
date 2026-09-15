# Music Workbench

> Experiment: from concept conversation to tested, working code with no human reading
> the design docs, plans, or code.

## The protocol

- A human and a model have **concept conversations**.
- A model writes the **design docs** from those conversations.
- A model decomposes a design into **work-plan steps** which include tests.
- A model executes one fully-specified step; every step is gated by `./bin/green` —
  the test suite, not a human's reading.
- **No human reads the design docs, the plans, or the code.** The human reads the
  conversation, the gate output, and the running app.

What the human *does* do is the part that is easy to understate: they steer in
conversation, make the scope and priority calls, run the thing, and own the
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
  desktop app which was also produced by this process.
  The tkinter code itself is not in this repository, but its
  design doc is (`design/music_app.md`), and the score-text libraries
  (`libs/music_parser`, `libs/pitch`) are shared by both.

## 4. Provenance

This repository was carved out of a larger private monorepo. The music web app
and the dev test harness that gates it are complete and green here [not tested
yet - CJP]. Left
behind, deliberately: the tkinter desktop front-end this app was ported from,
and other code that lived in the same monorepo. The git
history in this repository starts at the carve-out — it is not the history of
the original work.

## 5. Chris's thoughts

The above text was written by LLM and lightly edited. This section is by the
human.

I started this project with several goals:
 - Explore the limits of human-out-of-the-loop code production
 - Develop a fast, low-effort, and reasonably inexpensive pipeline for making my own apps
 - Develop a music-and-animation suite from the ground up (very much still in progress)

The project grew: I have several general-purpose design concepts and data structures, and I'm working on my own app to replace Claude Code with a web app using the Anthropic SDK. I expect that app to support the above workflow, orchestrating the LLM through the steps via a utility model rather than a conversational model.

I'm not trying to make commercial-quality code. There's no security or login on the design system or apps, no i18n or a11y, etc., etc. My goal is a system that lets me (and maybe eventually anyone who can think clearly about what they want) develop their own personal alternatives to commercial apps, even large and special-purpose apps.

My Claude Code is fairly chatty; it will talk through its decisions and ask me on judgment calls. I do engage with that text. If it says something that's inconsistent with my mental picture of how the design works, I'll follow up on that conversationally (still not reading the design artifacts directly).

I'm using Claude because I like talking with it and I trust its engineering (which is not to say I trust its outputs!) For now I'm not interested in integrating other LLMs.

The final success criteria:
 - Do I get apps that I enjoy using and do everything I want?
 - Is the app creation process reasonably inexpensive in dollars, calendar time, and human babysitting?
 - Can I stay entirely at the UX-and-architecture conversational level, never looking at the artifacts?

Lessons learned:
 - The design docs have evolved to be more and more chatty and history-rich. I'll need to prompt it to keep them lean and present-tense.
 - I can ask for features like libraries implementing arcane math (not in this repo yet) and get them coded even if I couldn't code them myself. (FWIW I asked Claude how maintainable they were; it said they were well-structured but would need a human or LLM math expert.)
 - Some bugs can last a surprising length of time before I or Claude discovers them.
 - I'm still working on how to decompose large apps, moving toward adding more layers: concept -> architecture -> design -> plans -> code, where architecture is a new layer of artifacts (which I won't look at).
 - I've tried having design/concept conversations with Sonnet, and it's just not smart enough to grasp the shape of what I want. Opus works great for that. I haven't experimented much with Fable; the few times I've used it, it's seemed very "thoughtful" but kind of runs ahead rather than collaborating with me to create the app and architecture I want.

Prior experience:
 - I generated several thousand lines of code with Gemini while at Google.
 - I used the DreamHost AI to generate a somewhat complicated website (https://codesmusic.com/) - it doesn't even give access to the artifacts. (My kid wanted to put his own music (note-by-note) into Minecraft resource/data packs.)
 - I'm a software engineer with decades of experience; I have a feel for elegant architectures and data handling that probably informs what I ask for and how I specify it.
