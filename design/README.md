# Design docs

This repository is a music workbench: a browser-served score editor and
player (`/music`) built on a small set of pure Python libraries under
`libs/`, a thin Flask runtime under `web/`, and a test harness that gates
every change. Each doc below owns a slice of that system; read `testing.md`
first — it is the entry point, because it specifies the gate every other
piece of code must pass. Every library under `libs/` also carries its own
`README.md` describing its API; this map says which design doc governs the
*decisions* behind a library, not its call signature.

## What question each doc answers

| Doc | Question it answers |
|---|---|
| `README.md` | this file — what's in the repo, and which doc owns which code |
| `testing.md` | what must every library ship, and what does the harness do with it? |
| `web-runtime.md` | how is a web page served, and what must a served asset guarantee? |
| `work-plans.md` | what discipline turns a design doc into committed code? |
| `music-web.md` | how does the browser editor stay identical to what it renders (WYHIWYG)? |
| `music_app.md` | how do the music libraries (parser, pitch, render, scores) fit together? |
| `music-web-files.md` | how does the web music app read and write `.music` files on disk? |
| `instruments.md` | how is the instrument-configuration UI built and wired? |
| `midi-values.md` | what MIDI parameters does an instrument expose, and how are they controlled? |
| `text_format.md` | what is the `.music` score text format? |

## What owns what

| Code | Owning doc(s) |
|---|---|
| `libs/test_harness`, `libs/demo`, `libs/live_gate`, `libs/dev_test` | `testing.md` |
| `libs/web_runtime` | `web-runtime.md` |
| `libs/seed_registry`, `libs/error_report`, `libs/ast_query` | no dedicated design doc — see the library's own `README.md` |
| `libs/music_parser`, `libs/pitch`, `libs/tempo_map` | `text_format.md`, `music_app.md` |
| `libs/music_render` | `music_app.md`, `music-web.md` |
| `libs/music_web_instruments` | `instruments.md`, `midi-values.md` |
| `libs/music_scores` | `music-web-files.md` |
| `web/server.py`, `web/apps/{fs,_common,manifest}.py`, `web/static/{fs_client,reload}.mjs` | `web-runtime.md` |
| `web/apps/music.py`, `web/music.html`, `web/static/music_app.js` | `music-web.md`, `music-web-files.md` |
| `web/static/music_audition.mjs`, `web/static/curve2d.mjs` | `instruments.md`, `midi-values.md` |
| `web/static/error_overlay.mjs` | no dedicated design doc — pairs with `libs/error_report`'s own `README.md` |
| `web/smoke/*.py` | `testing.md` (the Domain-2 smoke harness) |
| `bin/dev`, `bin/green`, `bin/serve`, `bin/look`, `bin/music_probe`, `bin/gate_lock.sh` | `testing.md` (`bin/dev`), `web-runtime.md` (`bin/serve`); `bin/look` has no dedicated doc — see `libs/ast_query/README.md` |

## Reading order

Start at `testing.md` to understand the gate, then `web-runtime.md` for how
anything gets served at all. `work-plans.md` is process, not runtime, and can
be read independently. The `music_*`/`instruments`/`midi-values`/
`text_format` docs form one cluster describing the shipped app; `music-web.md`
is that cluster's hub and cross-references the rest.
