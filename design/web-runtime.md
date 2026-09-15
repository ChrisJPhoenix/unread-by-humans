# Web Runtime — Design

**Date:** 2026-06-04
**Status:** Built — loopback runtime + path-traversal guard implemented. Project-wide
convention for every web page served here.

---

## The declaration

Every web page in this project is **served by a small local Flask (Python)
app** — never opened as `file://`. The Flask app gives the page's JS a
**filesystem affordance**: an HTTP API through which browser JS reads, writes,
and lists files within the repository root. In effect, JS gets a controlled
view of the Linux directory it lives in.

This is assumed by all web artifacts. A page may declare which paths it touches,
but it never has to ask "can I reach the filesystem?" — the answer is always
yes, through the affordance.

Run the server with the project venv per `CLAUDE_ALWAYS.md`: `venv/bin/python`.

---

## Why

Opening pages as `file://` boxes them into the browser sandbox: no disk writes,
clipboard/download friction, no way for the app to leave artifacts Claude can
read. Serving them from a local Flask app with an fs affordance unlocks, with no
download/upload dance:

- **Direct persistence** — project save/load.
- **Logs Claude can find** — the app drops traces/state where the filesystem
  tools see them directly.
- **Asset loading** — read models, images, saved curves from the tree.

The reload-and-retry workflow is unchanged: Flask serves the page with **no
build step**, so `^R` still reloads instantly.

---

## Scope and safety

- **Dev-only, localhost.** This is a development tool, not a production server.
  Bind to loopback.
- **Confined to the repository root.** Every path in the API is **relative to
  the project root**. Absolute paths and any `..` that escapes the root are
  **rejected** by `resolve_in_root`, the one piece of pure logic that must be
  tested (see *Testing*). A second, **global `before_request` guard** in
  `create_app` independently rejects (HTTP 400) any request whose URL *path*
  contains a `.` or `..` segment — a defense-in-depth backstop for path-segment
  inputs that Werkzeug decodes past the router before `resolve_in_root` ever
  runs. The two guards are complementary: `resolve_in_root` covers `?path=`/
  JSON-body inputs at each handler; the global guard covers URL-path segments
  for every app at once.
- Not an authentication boundary; it assumes a trusted local user.

---

## Invariant: served assets must be version-tracked

**An app must never serve a file out of an untracked or `.gitignore`d path.**
Every file that contributes to what a page *does* — the HTML shell, its JS/CSS,
vendored libraries, and any data file the server reads at request time (models,
images, scenes, saved curves, instrument samples) — **must be tracked in git.**

**Why.** Each worktree is a checkout that eventually merges into the main
branch. A file living under a gitignored path (or an untracked file that is
never committed) exists *only in the working tree where it was created*; the
merge drops it. So an app that serves such a file behaves one way in the
worktree and a **different way once it reaches `main`** — its behavior silently
changes out from under you. That is precisely the staleness/desync failure this
runtime is meant to make impossible, so it is ruled out at the source: if a file
is load-bearing for an app, it is committed; if it is committed, every worktree
and `main` agree on it.

**Outputs are the exception, and only outputs.** `tmp/`, `logs/`, `incidents/`
and the like are correctly gitignored: they are runtime *output*, written by the
app, never read back as behavior-defining *input*. The line is direction of
data flow — an app may *write* into an ignored path, but must never *serve from*
one. (The fs affordance can still read/write ignored paths for scratch and
incident bundles; this invariant constrains only what defines a page's served
behavior.)

**Enforcement.** Two checks, both cheap:

- **Served roots are never ignored.** At startup the runtime runs
  `git check-ignore` over each app's declared served roots (its page file, its
  `static`/asset directories) and **fails loudly** if any is ignored — turning a
  silent blind spot into a visible config error rather than a behavior change
  discovered after a merge.
- **New assets must be committed before merging to `main`.** An
  untracked-but-not-ignored asset (a freshly copied image) is fine *in
  progress* — it is visible via `git status --untracked-files=all` — but it has
  no blob in any commit, so merging to `main` would drop it. Whatever merges a
  branch to `main` should refuse, or at minimum warn, when an app's served tree
  contains untracked files.

This invariant is what lets git be trusted as the single content-truth: because
nothing load-bearing hides outside version control, "what git sees" equals
"what the app serves."

---

## API surface (proposed)

The server does two jobs: serve the page(s), and expose the fs affordance.

| Endpoint | Purpose |
|---|---|
| `GET /` and static serving | serve the page and its assets |
| `GET /fs/read?path=REL` | return the file's contents |
| `POST /fs/write` `{path, contents, noOverwrite?}` | write a file, creating parent dirs; `noOverwrite:true` fails if the path exists (used for never-overwrite incident bundles) |
| `GET /fs/list?path=REL` | directory listing (names + is-dir + size) |
| `POST /fs/mkdir` `{path}` | create a directory (parents ok) |

Higher-level endpoints may wrap these for convenience (e.g. an incident-bundle
write), but everything reduces to the fs affordance above. All `path` values are
project-relative; the server resolves them against the repository root and
applies the traversal guard before any I/O.

### JS client wrapper

Pages use one small client object that hides the HTTP, returning promises:

```js
fs.read(path)                       // -> string
fs.write(path, contents, opts={})   // opts: { noOverwrite }
fs.list(path)                       // -> [{name, isDir, size}, ...]
fs.mkdir(path)
```

This `fs` object is the single affordance pages depend on. Swapping the
transport (Flask now, something else later) does not touch page code.

---

## Single-sourcing constants: Python → JS

**A literal that exists in both Python and JS must live in Python only; JS reads
it at runtime.** When a page needs a value the Python side already defines (a
tuning constant, a default, a limit), do **not** copy the literal into a `.js`/
`.mjs` file. Two copies of the same number is a silent-drift hazard — exactly the
desync this runtime exists to rule out (cf. the version-tracking invariant
above), but for *values* instead of *files*.

**Mechanism — a server-generated ES module.** An app exposes a small route that
renders a whitelist of Python constants as an importable ES module:

```python
# in the app blueprint (web/apps/<app>.py)
_JS_CONSTANTS = {"SOME_CONSTANT": some_module.SOME_CONSTANT}  # references the live Python value

def render_js_constants_module(constants: "dict[str, float]") -> str:
    """Render a name→number mapping as `export const` lines (json.dumps → safe JS literal)."""
    return "".join(f"export const {n} = {json.dumps(v)};\n" for n, v in constants.items())

@bp.route("/<app>/js_constants.mjs")
def js_constants():
    return Response(render_js_constants_module(_JS_CONSTANTS), mimetype="text/javascript")
```

A page's module then imports it like any other:

```js
import { SOME_CONSTANT } from '/<app>/js_constants.mjs';   // value originates in Python
```

**Importer constraint — only browser-only modules may import the bridge.** The
route path is an *absolute server path* (`/<app>/js_constants.mjs`) that resolves
only when a browser fetches it. The Node `node:test` runner (Domain 3) imports
`.mjs` modules straight off the filesystem with **no server**, so a static
`import … from '/<app>/js_constants.mjs'` in any module reached by a `*.test.mjs`
fails to resolve (and a dynamic-import-with-`try/catch` fallback merely yields
`undefined` under Node — a silent hazard, not a fix). Therefore: a **pure,
Node-unit-tested `.mjs`** must **not** import the bridge — keep it import-free
and Node-safe. Put the bridge import in a **browser-only ES module** that the
test runner never loads (e.g. a DOM-facing module, not the pure one it depends
on). Do not "re-export the bridged constant through the shared pure module" —
that drags the unresolvable import into the Node-tested file.

**Why a module, not a `window.*` injection.** Pages here load as ES modules, so
an `import` is the native fit: it resolves synchronously from the importing
module's perspective (the browser fetches the route during module resolution),
needs no global, and has none of the load-order fragility of splicing an inline
`<script>` ahead of the deferred module scripts (contrast the reload-client
`after_request` inject, which is fine because order doesn't matter for it). The
generated module is **computed from tracked Python**, so it does not violate the
"served assets must be version-tracked" invariant — there is no untracked served
*file*; the source of truth is committed code.

**Test the bridge, not the literal.** The smoke check asserts the emitted module
*contains the live Python value* — e.g. that
`f"export const SOME_CONSTANT = {json.dumps(some_module.SOME_CONSTANT)};"`
is a substring of the response — so the test itself hardcodes no number and the
single-source contract is self-enforcing.

**Scope.** Only constants that genuinely exist in *both* worlds go through the
bridge. JS-only values (pixel nudges, UI-layout constants with no Python
counterpart) stay in JS; Python-only values stay in Python.

---

## Implementation

Concrete locked decisions:

- Bind host **127.0.0.1 (loopback only)**, hardcoded port **5050**. There are no
  per-worktree ports, no port registry, and no freshness dashboard — a single
  server on 127.0.0.1:5050 serves everything.
- Server lives at `web/server.py`; `web/server.py` exposes `create_app(project_root=PROJECT_ROOT) -> Flask` (the module global is the default; tests pass an isolated temp root), and the `web/smoke/*.py` per-project smoke tests (entry point `web/smoke/_harness.py`) exercise the app via the Flask test client. JS client at `web/static/fs_client.mjs`, served at `/static/fs_client.mjs`. `GET /` serves a minimal **app index** built from `web/apps/manifest.py`.
- `create_app` is a **thin assembler**: it registers the global path-segment
  traversal guard and one Flask **Blueprint per app** — `web/apps/{fs,music}.py`,
  each exposing a `make_blueprint(project_root, …)` factory — so every app owns
  its own route file (no shared merge-magnet module). Shared route helpers
  (`safe_path`, `bad_request`, `conflict`, `now_iso`, `usage_total`,
  `entry_info`) live in `web/apps/_common.py`. `create_app` also takes an
  `apps=` filter (default: all registered apps) — the seam by which a second
  app is added without touching the assembler itself.
- **All four endpoints** are implemented: `GET /fs/read`, `POST /fs/write` (with `noOverwrite` → **409** on an existing path), `GET /fs/list`, `POST /fs/mkdir`.
- The path-resolution guard is `libs/web_runtime/resolve_in_root(root, rel)` raising `PathTraversalError`, re-exported from `libs.web_runtime`, tested by `libs/web_runtime/test_primary.txt`. The complementary global URL-path-segment guard is a small inline `before_request` in `web/server.py`'s `create_app` (not a `libs/` module); it returns HTTP 400 and is exercised via a web smoke case using a URL-path-segment traversal attempt.
- Run via venv: `venv/bin/python web/server.py`.

**Single-server model and nonce-based auto-reload.** The server runs on a
hardcoded 127.0.0.1:5050; there are no per-worktree ports, no registry file, and
no freshness broker. A foreground `bin/serve` supervisor launches the server and
restarts it when the flag file `tmp/serve/restart` appears. Pages auto-reload
via a `GET /sse-reload` server-sent event stream: a per-process `SERVER_NONCE`
(module-level `secrets.token_hex(16)`) is emitted as the first SSE event; the
EventSource client at `web/static/reload.mjs` compares it against the value
seen at last load and calls `location.reload()` on a mismatch. The reload
client is injected into every HTML response by an `after_request` hook in
`create_app` (looks for `</body>` and splices in a `<script type="module">` tag
before it).

**Supervisor auto-handoff.** A directly-launched `web/server.py` (e.g.
`python web/server.py` after a reboot) now re-execs into `bin/serve`
automatically, so the supervised auto-restart path is always active. The
handoff fires from the `__main__` block only — after `argparse` so `-h/--help`
still work, before any port logic so a bare process never touches the port. It
is governed by a pure, harness-tested predicate: `bin/serve` exports
`MOVIE_SERVE_SUPERVISED=1` into the child process so the child does **not**
re-exec again (preventing an infinite loop). Setting `MOVIE_SERVE_NO_SUPERVISOR`
in the environment is an explicit opt-out for callers that want the bare-server
behavior. Because the handoff is behind the `__main__` guard, importing
`web.server` (e.g. by the web smoke harness via `create_app`) is completely
unaffected.

---

## Testing

- **Path-traversal guard** is pure (string in → allowed/rejected + resolved
  path) and belongs in a `libs/`-style module so it runs under `bin/dev test`:
  assert that `../`, absolute paths, and symlink-escape attempts are rejected,
  and that legitimate nested relative paths resolve under the repository root.
- The HTTP layer itself is thin glue; keep logic out of it so the guard and any
  bundle-assembly helpers are testable without a live server. The guard is
  covered via `libs/web_runtime/test_primary.txt`; the Flask app and JS client
  are intentionally not covered directly (thin glue).

---

## What this design does *not* cover

- A production deployment story (this is a local dev runtime).
- Authentication / multi-user access.
- The specific Flask app's file location and process management (an
  implementation detail; run it via the venv).
