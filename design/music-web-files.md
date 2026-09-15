# Music Web — Disk-Backed Score Files (feature parity with the desktop app)

**Date:** 2026-07-02
**Status:** **Built & green** (2026-07-02). Phases 0–2 complete: the pure
`libs/music_scores/` core (Domain 1), the path-gated + atomic file routes in
`web/apps/music.py` (Domain 2 smoke), and the browser-only File-cluster UI +
disk autosave in `web/music.html` / `web/static/music_app.js` (untested-by-design)
are all landed; `localStorage` is fully removed. Phase 3 (DELETE / rename /
`?file=` deep-link) remains optional/deferred. The companion work plan stays
in the private monorepo this app was carved out of.
**Hub:** `design/music-web.md` (this is a sub-doc of it).
**Reference (do not edit):** `design/music_app.md`, `music/music.py`,
`music/session_store.py`.

---

## Why

The web port (`/music`) reached instrument/play/render parity with the desktop
`bin/music` app, but it has **no file handling at all**: the score text lives
only in browser `localStorage` (`SCORE_KEY = 'music.score.v1'`), the toolbar is
Play / Stop / FLAC only, and there is no way to open, name, switch between, or
persist `.music` files on disk. The user's `happy.music` / `Used to know.music`
are unreachable from the web app.

This spec closes that gap to reach **file-handling parity with the desktop app**,
under two hard requirements from the user:

1. **All score data lives on disk. No browser `localStorage` at all** — the
   `SCORE_KEY` persistence is removed entirely.
2. **A different UI is fine, but every desktop file feature must be present.**

Instrument editing, Play-from-caret, Stop, and FLAC are already built and are
**not** re-specified here; this doc is only the file/persistence layer.

---

## Desktop file features → web equivalents (the parity checklist)

Cataloged from `music/music.py` and `music/session_store.py`:

| Desktop feature (`music/music.py`) | Web equivalent (this spec) |
|---|---|
| **Load** button → `askopenfilename` (`*.music *.txt`) → read into editor, set `_current_file` | **File switcher** dropdown listing the scores directory; picking one opens it (`GET /music/score`) |
| **Save** → write to `_current_file`, else `asksaveasfilename` (default `.music`) | **Autosave** to the current file on every edit (debounced) **+** explicit **Save** button that force-flushes and reports "Saved" |
| **Save As** (implicit, when no current file) | **Save As** button → prompt for a name → create + switch (`POST /music/scores/new`) |
| **Save FLAC** → render + `asksaveasfilename` (`.flac`) | Already built (`GET /music/flac` download) — unchanged |
| **Play** from caret section, **Stop** | Already built (`POST /music/play`) — unchanged |
| **Instruments** window | Already built (docked panel) — unchanged |
| command-line `file` arg → open on launch | **`GET /music?file=<name>`** deep-link opens that score (optional, Phase 3) |
| **last-file memory** (`appdata/musicdata/session.json`) → auto-reload on next launch | **`appdata/webmusicdata/session.json`** stores the current score's *relative name*; startup reopens it |
| `_current_file` + status line ("Loaded X" / "Saved X") | **Current-file indicator** in the toolbar (name + saved/dirty dot) |
| **Unsaved-changes guard** on close (Ctrl-W / window close) | **Obviated by autosave** — see *Locked decisions* #2 (intentional divergence) |
| *(desktop has no explicit "New")* | **New** button → creates & opens a fresh `untitled*.music` (added for the web, since there is no OS "save-new-file" dialog) |

---

## Locked decisions

1. **Scores live in `appdata/webmusicdata/scores/`.** A new directory, path-gated
   through `libs/web_runtime`'s `resolve_in_root` exactly like
   `appdata/webmusicdata/instruments.json`. Score files are `*.music` (canonical) and
   `*.txt` (accepted on read/list). The desktop `appdata/musicdata/` and the repo-root
   `.music` files are **never** read or written by the web app — same isolation
   rule as instruments (`design/music-web.md` §Asset directory). To use an
   existing `.music` file in the web app, copy it into `appdata/webmusicdata/scores/`.

2. **Autosave-on-everything — the disk is always in sync with what the user
   sees (LOCKED). No Save button.** There is no in-memory-only buffer and no
   `localStorage`. Two things persist continuously to disk:
   - **Score text** → `PUT /music/score` on a **short** debounce (~300 ms,
     coalescing only rapid keystrokes).
   - **View state (caret offset + scroll position)** → `POST /music/session` on
     a short debounce as the caret moves / the editor scrolls — cheap, because
     it rewrites only `session.json`, never the score file.

   Instruments already persist to `appdata/webmusicdata/instruments.json` and the
   current-file selection to `session.json`, so the **whole visible editor state
   is on disk at all times**.

   **Exit flush (this replaces the Save button).** On the way out — reload,
   navigation, or close — the client flushes any pending debounce so nothing in
   the last window is lost. This runs on `visibilitychange`→`hidden` and
   `pagehide` via `navigator.sendBeacon` (or `fetch(..., {keepalive: true})`),
   the events/transports that reliably complete during unload. **No confirmation
   dialog is shown:** we deliberately do *not* set `beforeunload.returnValue`
   (setting it is the only way to trigger the browser's un-customizable "Leave
   site? Changes may not be saved" prompt, and we don't want it). This is the
   correct realization of "tie the leaving-page event to a flush and let the page
   go." Because the short-debounce autosave wrote moments earlier, the exit flush
   is only the last-few-hundred-ms delta.

   The **Save button is removed.** An optional, de-emphasized "saved ✓ /
   saving…" indicator may remain for reassurance, but there is no manual save
   action, and the desktop's *unsaved-changes-on-quit* guard is gone.

   ### Reload contract (why the sync must be tight)

   The single-port web runtime (`design/web-runtime.md`) can
   be **restarted at any moment** — the `bin/serve` supervisor restarts the one
   `:5050` server and every open page auto-reloads over SSE. So `/music` can
   reload without warning, not just on a user `^R`. **The contract: after any
   reload the page reappears exactly as the user last saw it** — same score
   text, same current file, same instruments, **and the same caret position and
   scroll offset** — all restored from disk on startup (decision #3).
   *(This web version restores caret + scroll — a deliberate divergence from the
   desktop, `design/music_app.md` §Startup, which does not, because the desktop
   is never restarted involuntarily; here an out-of-band restart must be
   invisible to the user.)* Residual risk: an *involuntary* restart landing
   inside the sub-second debounce window can lose the last few keystrokes;
   accepted as the cost of not writing synchronously per keystroke, and
   minimized by the short debounce plus the exit-flush beacons.

3. **There is always exactly one current file.** The web editor never edits a
   nameless buffer (autosave needs a target). Startup resolution order:
   1. If `GET /music?file=<name>` is present and valid → open it (Phase 3).
   2. Else if `session.json` names a current file that still exists → open it.
   3. Else if the scores directory is non-empty → open the first file
      (case-insensitive sort).
   4. Else → create and open `untitled.music` (empty).

   When step 2 reopens the session's current file, the saved **caret + scroll**
   from `session.json` are restored too (decision #2 reload contract). Opening
   any *other* file (switch, first-file fallback, fresh bootstrap) starts at the
   top.

4. **The session stores a *relative name*, not an absolute path.** Unlike the
   desktop `session_store` (which stores an absolute path), the web session
   records just the bare filename within `appdata/webmusicdata/scores/`. Portable,
   path-guarded, and can never point outside the scores directory.

5. **Pure name/session logic is a harness-tested library; file I/O stays in the
   route as untested glue** — mirroring the instruments split
   (`libs/music_web_instruments` pure + route glue smoke-tested). New pure lib:
   `libs/music_scores/` (Domain 1). The `os.listdir` / read / atomic-write /
   session read-write live in `web/apps/music.py` and are covered by the
   Domain-2 smoke test.

6. **New is name-first.** The **New** affordance opens a *name-or-cancel* dialog;
   the user types a name (Cancel = no-op). The client `POST`s it to
   `/music/scores/new`; an existing name returns **409** and the dialog reports
   the clash. There is **no** auto-`untitled-N` naming. The lone exception is the
   empty-scores **bootstrap** (decision #3 step 4): the client silently creates
   `untitled.music` so autosave always has a target; if `untitled.music` already
   exists it is simply opened. (Save-As / duplicate-under-a-new-name is not part
   of this version — see *Deferred* / Phase 3.)

---

## New library: `libs/music_scores/` (pure, Domain 1)

No Flask, no filesystem, no `music/` imports — pure string/list logic.

| Function | Purpose |
|---|---|
| `is_valid_score_name(name) -> bool` | True iff `name` is a bare filename (no `/`, `\`, `..`, no leading dot) ending in `.music` or `.txt`. The traversal guard's first line of defense. |
| `normalize_score_name(name) -> str` | Trim; append `.music` if it has no `.music`/`.txt` extension. Raises `ValueError` if the result is not `is_valid_score_name`. |
| `score_rel_path(name) -> str` | `appdata/webmusicdata/scores/<name>` after `normalize_score_name`. Raises `ValueError` on an invalid name (defense-in-depth before `resolve_in_root`). |
| `filter_and_sort_scores(names_json) -> str` | Keep only `.music`/`.txt`, drop dotfiles, sort case-insensitively. Takes/returns a **JSON-encoded list of strings** (not a raw `list[str]`) because the plan-file harness grammar has no list literal — the `_json` boundary convention `libs/music_web_instruments` also uses; the route glue `json.dumps`/`json.loads` around it. |
| `parse_session(text) -> dict \| None` | Extract `{last_file, caret, scroll}` from session JSON text — `last_file` validated (else the whole result is unusable → `None`), `caret`/`scroll` coerced to non-negative ints (default 0). `None` on missing/corrupt. Never raises. |
| `build_session(current, caret=0, scroll=0) -> dict` | `{"version": 1, "last_file": current, "caret": caret, "scroll": scroll}`. |

Scalar boundary values are plain strings / ints; **lists and dicts are carried
as JSON strings** (the plan-file grammar has neither literal), so dict-returning
`parse_session`/`build_session` are asserted at the `None`/no-raise level.
`test_primary.txt` covers: valid/invalid names (including `../`,
`a/b`, `.hidden`, extension-less), normalize round-trips, filter/sort ordering,
and session parse of good/missing/corrupt/traversal payloads (incl. caret/scroll
coercion + defaulting).

---

## Routes (added to `web/apps/music.py`)

All paths resolve through `resolve_in_root(project_root, score_rel_path(name))`
so a crafted name can never escape `appdata/webmusicdata/scores/`. Writes use a new
`_atomic_write_text` helper (sibling of the existing `_atomic_write_json`).
Constant: `_SCORES_DIR_REL = "appdata/webmusicdata/scores"`,
`_SESSION_REL = "appdata/webmusicdata/session.json"`.

| Route | Method | Body / Query | Returns | Notes |
|---|---|---|---|---|
| `/music/scores` | GET | — | `{"files":[...], "current": name\|null, "caret": int, "scroll": int}` | Scores dir (`filter_and_sort_scores`) + session current file & view state. Missing dir/session → `[]`, `null`, `0`, `0`. |
| `/music/score` | GET | `?name=<n>` | `{"name": n, "text": "..."}` | 400 invalid name; 404 missing file. |
| `/music/score` | PUT | `{"name","text","make_current"?}` | `{"ok": true}` | Atomic write. `make_current` (default false) updates `session.json`. Used by autosave. 400 invalid name/body. |
| `/music/scores/new` | POST | `{"name", "text"?}` | `{"name": name}` | **Name required** (name-or-cancel dialog). Existing name → 409; invalid → 400. Writes the (empty or given) file and sets it current. |
| `/music/session` | POST | `{"current"?, "caret"?, "scroll"?}` | `{"ok": true}` | Merge into `session.json`. `current` (file-switch) → 404 if the file is missing; `caret`/`scroll` (cursor/scroll, debounced) are cheap and rewrite only the session. |
| `/music/score` | DELETE | `?name=<n>` | `{"ok": true}` | **Phase 3 (optional).** Removes a score; if it was current, current falls back per decision #3. |
| `/music/score/rename` | POST | `{"from","to"}` | `{"name": to}` | **Phase 3 (optional).** |

`GET /music/parse`, `/music/audition`, `/music/play`, `/music/flac` are
unchanged — Play/Audition/FLAC still operate on the editor buffer's `text`,
which is now the on-disk-backed buffer.

**Error posture** (matches existing routes): bad JSON body → 400 (never 500);
invalid/traversing name → 400; missing file on GET → 404; naming collision on
`new` → 409.

---

## UI (browser-only glue — `web/music.html` + `web/static/music_app.js`)

Untested-by-design, like the rest of `music_app.js`. A **File** cluster is added
to the toolbar (left of Play):

- **Score `<select>`** — the switcher **and** the current-file display in one
  control: it lists every `.music` (and `.txt`) file from `GET /music/scores`
  and shows the current file as the selected option. Changing it: flush the
  outgoing file → `GET /music/score` (new) → `POST /music/session {current}` →
  replace textarea → reparse (the new file opens at the top).
- **New** button → a **name-or-cancel** dialog (`prompt()` is acceptable) →
  `POST /music/scores/new {name}` → refresh the dropdown → open the new (empty)
  file. A 409 clash re-opens the dialog noting the clash.
- *(optional)* a small, de-emphasized **saved ✓ / saving…** indicator.
  **There is no Save button.**
- *(Phase 3, deferred)* Delete / Rename / Save-As-duplicate controls.

**`init()` changes:**

- **Remove** the `SCORE_KEY` / `localStorage` read and the per-keystroke
  `localStorage.setItem` entirely. The textarea, current file, caret, and scroll
  are all seeded from disk on startup (decision #3).
- The `input` handler schedules the existing parse update **and** a
  short-debounce `PUT /music/score` autosave.
- Caret events (`selectionchange` / `keyup` / `click`) and `scroll` schedule a
  short-debounce `POST /music/session {caret, scroll}` — cheap, session-only
  writes.
- On startup, restore the caret offset and `scrollTop` from the values returned
  by `GET /music/scores`.
- **Exit flush:** on `visibilitychange`→`hidden` and `pagehide`, `sendBeacon`
  (or `fetch` `keepalive`) any pending score + session writes so a reload /
  navigation / close loses nothing. Do **not** set `beforeunload.returnValue` —
  no confirmation dialog (decision #2).

`web/music.html` gains the toolbar markup and passes the new elements into
`init({...})` alongside the existing `textarea` / `parseResultEl` / `statusEl` /
`panelEl`.

---

## Asset directory

This feature adds two entries to `appdata/webmusicdata/`:
`appdata/webmusicdata/scores/` (the `.music`/`.txt` score files, with a `.gitkeep`) and
`appdata/webmusicdata/session.json` (`{version, last_file, caret, scroll}` — the
current-file + view-state memory, a relative name, never an absolute path). The
hub's `design/music-web.md` §Asset directory now lists the full `appdata/webmusicdata/`
layout including these two — see it for the canonical layout.

---

## Testing map

| Artifact | Domain | Runner |
|---|---|---|
| `libs/music_scores` (name/session pure logic) | 1 | `bin/dev test` (`test_primary.txt`) |
| `web/apps/music.py` new routes (list/read/write/new/session, guards) | 2 | `bin/dev test` (`web/smoke/music.py`) |
| `web/apps/music.py` DELETE/rename (Phase 3) | 2 | smoke |
| `web/music.html`, `web/static/music_app.js` File UI + autosave | browser-only, manual | — |

Domain-2 additions to `web/smoke/music.py`: create-via-`new` (with a name) →
appears in `GET /music/scores`; `POST /music/scores/new` with no name → 400;
`PUT` round-trips through `GET /music/score`; invalid name (`../etc`, `a/b`) →
400; missing file → 404; `new` name collision → 409; `POST /music/session
{current}` on a missing file → 404; `POST /music/session {caret, scroll}` then
`GET /music/scores` echoes them; bad JSON body → 400 (never 500).

Verify with `./bin/green`. The autosave/switcher UI is browser-only — confirm by
reloading `/music` and exercising New / switch / edit-and-reload (per
`design/web-runtime.md`), which the green gate cannot do.

---

## See also

- `design/music-web.md` — the web-port hub (this sub-doc's parent).
- `design/music_app.md` — the desktop app being matched (reference, do not edit).
- The phased, paste-ready build plan stays in the private monorepo this app
  was carved out of.
