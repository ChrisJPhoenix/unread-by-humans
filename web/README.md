# web/

Local dev Flask fs-affordance runtime, per `design/web-runtime.md`, serving the
one app carried by this repository: the music web port (`design/music-web.md`).

## Running

```
venv/bin/python web/server.py
```

or, for the supervised auto-restart loop that watches for a restart flag and
re-execs the server:

```
./bin/serve
```

`server.py` inserts the repo root on `sys.path` at startup, so it runs
correctly from any working directory.

## Binds

`127.0.0.1:5050` — loopback only, not reachable from the network.

## Pages and routes

| Path | What |
|---|---|
| `GET /` | Minimal app index (built from `web/apps/manifest.py`), linking to `/music` |
| `GET /music` | Music web app — WYHIWYG score editor + audition + play + FLAC (`web/music.html`; see `design/music-web.md`) |
| `POST /music/parse` | Parse score text `{text}` → sections/events/errors/beat-counts JSON; instruments from `appdata/webmusicdata/instruments.json` |
| `GET /music/instruments` | Read `appdata/webmusicdata/instruments.json` (empty list default if absent) |
| `POST /music/instruments` | Atomically write `appdata/webmusicdata/instruments.json` (path-guarded) |
| `POST /music/audition[?backend=fake]` | Render a single-instrument preview from the POSTed instrument spec → raw float32 PCM bytes, played via Web Audio; `?backend=fake` uses `FakeBackend` |
| `POST /music/play[?backend=fake]` | Render the score from the caret (`{text, caret}`) with the designed instruments → raw float32 PCM bytes, played via Web Audio; `?backend=fake` uses `FakeBackend` |
| `GET /music/flac[?text=…&backend=fake]` | 24-bit FLAC download of the `?text=` score (audio/flac, attachment); requires `soundfile` + FluidSynth + a soundfont — **untested by design** |
| `GET /music/scores` | List `.music`/`.txt` score files + session current file & caret/scroll view state |
| `GET /music/score?name=` | Read one score's text (400 invalid name, 404 missing) |
| `PUT /music/score` | `{name, text, make_current?}` — atomic-write a score (autosave); optional session update |
| `POST /music/scores/new` | `{name, text?}` — create a named score & set it current (400 no name, 409 collision) |
| `POST /music/session` | `{current?, caret?, scroll?}` — merge into `session.json` (404 missing current) |

The file routes resolve every path through `resolve_in_root` (`libs/web_runtime`)
composed with the score-path helpers in `libs/music_scores/`, and write via an
atomic write helper. See `design/music-web-files.md` for the full file-handling
contract, and `design/music-web.md` for the render pipeline and the
`FakeBackend` injectable-backend seam.

`appdata/webmusicdata/` is the asset dir for the music app: `instruments.json`
(designed instruments), `scores/` (the `.music`/`.txt` score files), and
`session.json` (current-file + caret/scroll memory).

A handful of dev-support routes also live in `web/server.py`: `GET /sse-reload`
(the nonce-based auto-reload SSE stream consumed by `static/reload.mjs`),
`GET /_test_crash` and `POST /_client_error` (error-surfacing test seams — see
`web/smoke/error_surfacing.py`).

## Filesystem API

| Endpoint | Purpose |
|---|---|
| `GET /fs/read?path=REL` | Return file contents as plain text |
| `POST /fs/write` `{path, contents, noOverwrite?}` | Write file, create parents; `noOverwrite:true` returns 409 if path exists |
| `GET /fs/list?path=REL` | Directory listing: `[{name, isDir, size}, …]` |
| `POST /fs/mkdir` `{path}` | Create directory (parents ok) |

All `path` values are project-relative. Absolute paths and any `..` that
escapes the project root are rejected with HTTP 400.

## Static assets

| File | Domain | Purpose |
|---|---|---|
| `/static/fs_client.mjs` | Domain 3 | JS filesystem client (ES module, also sets `window.fs`); tested via `fs_client.test.mjs` |
| `/static/reload.mjs` | Domain 3 | Nonce-based `EventSource` auto-reload client injected into every HTML page; tested via `reload.test.mjs` |
| `/static/error_overlay.mjs` | Domain 3 | Browser error/rejection overlay injected into every HTML page; tested via `error_overlay.test.mjs` |
| `/static/curve2d.mjs` | Domain 3 | Pure `Curve2D` model + spline math, exports `Curve2D`, `buildSegments`, `evaluate`; tested via `curve2d.test.mjs` |
| `/static/music_audition.mjs` | Domain 3 | Pure PCM-buffer math for the Web Audio transport (audition + play): `frameCountFromByteLength`, `deinterleave`, `applyFadeInOut`, `nextAuditionToken`, `isCurrent`; tested via `music_audition.test.mjs` |
| `/static/music_app.js` | browser-only, untested | Editor glue for `/music`: disk-backed score files (switcher / name-first New / short-debounce autosave + caret-scroll session autosave); Web Audio Audition + Play transports; docked instruments panel with a live "Edit" instrument editor; imports `music_audition.mjs` and `curve2d.mjs` |

`music_app.js` is untested by design (DOM + `AudioContext`), matching
`design/music-web.md`'s posture on the browser-only layer.

## Path-traversal guard

The load-bearing logic — `resolve_in_root` and `PathTraversalError` — lives in
`libs/web_runtime/` and is covered by `bin/dev test`. In addition, `create_app`
registers a single global `before_request` guard that rejects any request
whose URL path contains a `.` or `..` segment with HTTP 400 — a
defense-in-depth backstop for path-segment inputs. The HTTP layer in
`server.py` and the JS client in `static/fs_client.mjs` are intentionally not
covered by `bin/dev test`, consistent with the rest of this repo's
untested-by-design boundaries. See `web/smoke/README.md` for the Domain-2
smoke-test layout and contract.
