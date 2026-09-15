"""Local dev web server — thin assembler that wires per-app blueprints into one Flask app.

Run:
    venv/bin/python web/server.py
"""

import argparse
import faulthandler
import io
import json
import markupsafe
import os
import secrets
import signal
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Print the native/Python frame to stderr on a segfault (e.g. inside libfluidsynth).
# stderr may lack a real fileno() under the test harness — skip in that case.
try:
    faulthandler.enable()
except io.UnsupportedOperation:
    pass

from flask import Flask, abort, jsonify, request, Response
from werkzeug.exceptions import HTTPException
from libs import dev_serve, error_report
from web.apps import manifest
from web.apps._common import bad_request
from web.apps import fs, music

# Storm gate state — module-level so it persists across requests in one process.
_error_seen_keys: set = set()
_error_distinct_count: int = 0


def _error_log_path() -> str:
    """Absolute path to the per-port error log under movie/tmp."""
    return os.path.join(PROJECT_ROOT, "tmp", "errors-5050.log")


def _append_error_log(report: dict) -> None:
    """Write report to the error log when the storm gate allows it.

    Reusable by the errorhandler, pluck run_guarded, and /_client_error in later
    steps.  Updates the module-level seen_keys / distinct_count on emit.
    """
    global _error_seen_keys, _error_distinct_count
    key = error_report.storm_key(report)
    if not error_report.should_emit(key, _error_seen_keys, _error_distinct_count):
        return
    _error_seen_keys.add(key)
    _error_distinct_count += 1
    log_path = _error_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(error_report.format_report(report))
        fh.write("\n")

# Regenerated each process start; the SSE client reloads pages when this value changes.
SERVER_NONCE = secrets.token_hex(16)


def create_app(
    project_root: str = PROJECT_ROOT,
    apps: "tuple[str, ...] | None" = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    @app.before_request
    def _reject_path_traversal():
        """Return 400 for any request whose decoded URL path contains a '.' or '..' segment.

        A single global defense-in-depth guard: Werkzeug decodes percent-encoded
        slashes (%2F -> /) before routing, so a traversal slug becomes extra path
        segments that miss every route (404) instead of hitting resolve_in_root.
        This catches those before dispatch and returns the correct 400. Inputs passed
        as query params or JSON bodies are unaffected here and stay guarded by
        resolve_in_root at the handler.
        """
        segments = request.path.split("/")
        if ".." in segments or "." in segments:
            return bad_request("invalid path: path traversal detected")

    @app.before_request
    def _force_fresh_documents():
        # HTML pages get the live-reload script injected at response time, so the
        # file-based ETag/Last-Modified that send_file sets no longer match what we
        # actually serve. Strip conditional validators on document (navigation)
        # requests — those whose Accept header includes text/html — so the server
        # returns a full 200 we can inject into, never a 304 that lets the browser
        # reuse a stale, un-injected cached page. Sub-resource requests (JS modules,
        # CSS, images, fetch) don't send text/html in Accept, so their normal
        # ETag/304 caching is preserved.
        if "text/html" in request.headers.get("Accept", ""):
            request.environ.pop("HTTP_IF_NONE_MATCH", None)
            request.environ.pop("HTTP_IF_MODIFIED_SINCE", None)

    blueprint_thunks = {
        "fs": lambda: fs.make_blueprint(project_root),
        "music": lambda: music.make_blueprint(project_root),
    }
    if apps is None:
        selected_names = list(blueprint_thunks)
    else:
        requested = set(apps)
        selected_names = ["fs"] + [
            name for name in blueprint_thunks if name != "fs" and name in requested
        ]
    for name in selected_names:
        app.register_blueprint(blueprint_thunks[name]())

    @app.route("/")
    def _app_index():
        return _render_app_index(manifest.all_apps())

    @app.route("/sse-reload")
    def _sse_reload():
        def _event_stream():
            yield f"data: {SERVER_NONCE}\n\n"
            while True:
                time.sleep(2)
                yield "data: heartbeat\n\n"
        return Response(_event_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache"})

    @app.after_request
    def _inject_reload_client(response):
        # Inject the SSE auto-reload client into every HTML page, including pages
        # served via send_file (which set direct_passthrough=True). We must call
        # make_sequence() to buffer a direct_passthrough response before reading
        # it; get_data() raises RuntimeError on direct_passthrough otherwise.
        # The SSE event stream at /sse-reload is text/event-stream, not text/html,
        # so it is never matched here.
        if response.mimetype == "text/html":
            if response.direct_passthrough:
                response.make_sequence()
            body = response.get_data(as_text=True)
            if "</body>" in body:
                parts = body.rsplit("</body>", 1)
                # Tells the client which app (if any) owns this page, so
                # reload.mjs can filter the route-keyed "stale" SSE event to
                # just its own route (design/worktree-staleness.md decision 3).
                app_route_script = (
                    f"<script>window.__APP_ROUTE__ = "
                    f"{json.dumps(manifest.route_for_path(request.path))};</script>"
                )
                # Inline bootstrap: registers error hooks synchronously BEFORE
                # the overlay module loads, so crashes during bootstrap are still
                # caught and shown via the plain-pre fallback.  Must not
                # reference the module's exports (runs first).
                inline_bootstrap = (
                    '<script>'
                    '(function(){'
                    'var _fp=null;'
                    'function _fb(msg){'
                    'if(!_fp){'
                    '_fp=document.createElement("pre");'
                    '_fp.id="__error_overlay_fallback__";'
                    '_fp.style.cssText="position:fixed;top:0;left:0;right:0;z-index:999999;'
                    'background:#1a0000;color:#ff8080;padding:16px;'
                    'font:13px/1.5 monospace;white-space:pre-wrap;'
                    'max-height:50vh;overflow:auto;border-bottom:2px solid #ff4040;";'
                    'var t=document.body||document.documentElement;'
                    'if(t)t.prepend(_fp);'
                    '}'
                    '_fp.textContent+=msg+"\\n\\n";'
                    '}'
                    'window.addEventListener("error",function(ev){'
                    'if(window.__errorOverlayReady)return;'
                    '_fb("Error: "+(ev.message||String(ev.error)));'
                    '});'
                    'window.addEventListener("unhandledrejection",function(ev){'
                    'if(window.__errorOverlayReady)return;'
                    'var r=ev.reason;'
                    '_fb("Unhandled rejection: "+((r&&r.message)?r.message:String(r)));'
                    '});'
                    '})();'
                    '</script>'
                )
                response.set_data(
                    parts[0]
                    + app_route_script
                    + inline_bootstrap
                    + '<script type="module" src="/static/error_overlay.mjs"></script>'
                    + '<script type="module" src="/static/reload.mjs"></script>'
                    + "</body>"
                    + parts[1]
                )
            response.headers["Cache-Control"] = "no-store"
            response.headers.pop("ETag", None)
            response.headers.pop("Last-Modified", None)
        return response

    @app.errorhandler(Exception)
    def _handle_uncaught_exception(exc):
        # Pass werkzeug HTTP exceptions through unchanged so routing 404s/405s
        # and deliberate abort(4xx) keep their intended status and are not
        # reported as crashes.
        if isinstance(exc, HTTPException):
            return exc

        # Extract best-effort request payload for context.
        try:
            payload = request.get_data(as_text=True) or None
        except Exception:
            payload = None

        report = error_report.build_report(
            exc,
            path=request.path,
            url=request.url,
            payload=payload,
        )
        _append_error_log(report)

        formatted = error_report.format_report(report)
        if "text/html" in request.headers.get("Accept", ""):
            escaped = markupsafe.escape(formatted)
            html = (
                "<!DOCTYPE html><html><head><title>Server Error</title></head>"
                f"<body><pre>{escaped}</pre></body></html>"
            )
            return html, 500
        return jsonify(error_report.crash_envelope(report)), 500

    @app.route("/_test_crash")
    def _test_crash_seam():
        # Test seam for Domain-2 error-surfacing smoke — exists only to drive
        # the errorhandler via an HTTP request.  Inert without a recognized flag.
        mode = request.args.get("mode", "")
        if mode == "raise" or request.args.get("boom") == "1":
            raise Exception("synthetic test crash")
        if mode == "abort":
            abort(404)
        return "/_test_crash seam: pass mode=raise or mode=abort to exercise the errorhandler.", 200

    # Browser-error log sink (sources 1 & 2: window.onerror + unhandledrejection).
    # Intentionally excluded from the client fetch wrapper in error_overlay.mjs to
    # avoid reentrancy (a POST error here must not trigger another POST here).
    @app.route("/_client_error", methods=["POST"])
    def _client_error_sink():
        try:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                _append_error_log(body)
        except Exception:
            pass
        return "", 204

    return app


def _render_app_index(apps: list) -> str:
    """Return a minimal HTML app index page listing each app with its route and description."""
    items = "\n".join(
        f'<li><a href="{markupsafe.escape(a["route"])}">'
        f'{markupsafe.escape(a["name"])}</a>'
        f' — {markupsafe.escape(a["description"])}</li>'
        for a in apps
    )
    return (
        "<!DOCTYPE html><html>"
        "<head><title>movie apps</title></head>"
        f"<body><h1>Apps</h1><ul>{items}</ul></body>"
        "</html>"
    )


def _force_free_port(host: str, port: int) -> bool:
    """Terminate the process LISTENing on port and wait for it to release the port.

    Sends SIGTERM to the listener PID (from dev_serve.listener_pid), polls until the
    port is free, then escalates to SIGKILL if still held. Returns True if the port
    became free, False if no listener PID could be identified.
    """
    pid = dev_serve.listener_pid(port)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not dev_serve.port_in_use(host, port):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for _ in range(50):
        if not dev_serve.port_in_use(host, port):
            return True
        time.sleep(0.1)
    return not dev_serve.port_in_use(host, port)


def _install_thread_excepthook() -> None:
    """Route uncaught exceptions in any thread to the error log (process-wide backstop).

    Called once at server startup (inside __main__) so it does not become a
    side effect of create_app() during test-client construction.
    """
    import threading
    def _hook(args):
        if args.exc_value is not None:
            _append_error_log(error_report.build_report(args.exc_value))
    threading.excepthook = _hook


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the web dev server.")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Kill whatever process is already using the port, then start.")
    args = parser.parse_args()
    _install_thread_excepthook()
    dev_serve.reexec_into_supervisor_unless_managed(os.path.join(PROJECT_ROOT, "bin", "serve"))
    host = "127.0.0.1"
    port = 5050
    if dev_serve.port_in_use(host, port):
        if args.force:
            if not _force_free_port(host, port):
                print(dev_serve.busy_message(port, dev_serve.listener_pid(port)), file=sys.stderr)
                sys.exit(1)
        else:
            print(dev_serve.busy_message(port, dev_serve.listener_pid(port)), file=sys.stderr)
            sys.exit(1)

    create_app().run(host=host, port=port, debug=False)
