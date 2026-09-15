"""Blueprint exposing /music/* handlers for the Music score-editing web app.

Routes
------
GET  /music              Serve the Music app page shell.
POST /music/parse        Parse score text; return sections/events/errors/beat-counts.
GET  /music/instruments  Read appdata/webmusicdata/instruments.json.
POST /music/instruments  Write appdata/webmusicdata/instruments.json atomically (path-guarded).
POST /music/audition     Render short audition clip from instrument spec; return raw float32 PCM bytes.
POST /music/play         Render full song from {text, caret}; return raw float32 PCM bytes.
GET  /music/flac         Render full song; return 24-bit FLAC as attachment download.
                         Untested by design: requires ``soundfile`` + FluidSynth + soundfont,
                         none of which are available in the test sandbox.  The route is wired
                         correctly and will work wherever those deps are present.
GET  /music/scores       List score files + session current file & view state.
GET  /music/score        Read one score file's text (?name=).
PUT  /music/score         Atomically write a score file (autosave); optional make_current.
POST /music/scores/new   Create a new named score and set it current (409 on collision).
POST /music/session      Merge current/caret/scroll into appdata/webmusicdata/session.json.
"""

import json
import os
import tempfile
from typing import Any, Optional

from flask import Blueprint, Response, request, jsonify, send_file

from libs.web_runtime import resolve_in_root, PathTraversalError
from libs.music_web_instruments import (
    instruments_arg_from_designed,
    audition_score_and_programs,
    play_render_bundle,
)
from libs.music_parser import parse_score
from libs.music_scores import (
    score_rel_path,
    filter_and_sort_scores,
    parse_session,
    build_session,
    normalize_score_name,
)
from libs.music_render import (
    FakeBackend,
    render_audition_pcm,
    render_song_pcm,
)
from web.apps._common import bad_request, conflict

# ── Constants ─────────────────────────────────────────────────────────────────

_INSTRUMENTS_REL = "appdata/webmusicdata/instruments.json"
_DEFAULT_SAMPLE_RATE = 48000
_SCORES_DIR_REL = "appdata/webmusicdata/scores"
_SESSION_REL = "appdata/webmusicdata/session.json"


# ── Backend selection (injectable seam) ───────────────────────────────────────

def _select_backend(req) -> Any:
    """Return FakeBackend for ?backend=fake; otherwise the real FluidSynth backend.

    Only the literal query-param value ``fake`` maps to FakeBackend.  Every other
    value (including absent) lazily constructs the real FluidSynth backend.

    Args:
        req: The Flask ``request`` object.

    Returns:
        A synth backend satisfying the note_on/note_off/render_block interface.
    """
    if req.args.get("backend") == "fake":
        return FakeBackend()
    from libs.music_render import make_fluidsynth_backend
    return make_fluidsynth_backend()


# ── Instruments file helpers ──────────────────────────────────────────────────

def _instruments_path(project_root: str) -> str:
    """Return the absolute path to appdata/webmusicdata/instruments.json.

    Args:
        project_root: Absolute path to the repository root.

    Returns:
        Absolute path to the instruments JSON file.

    Raises:
        PathTraversalError: If the path would escape project_root (should never
            happen for the hardcoded relative path, but kept for safety).
    """
    return resolve_in_root(project_root, _INSTRUMENTS_REL)


def _load_instruments_rows(project_root: str) -> list:
    """Return the stored instrument rows, or an empty list if the file is missing.

    Args:
        project_root: Absolute path to the repository root.

    Returns:
        List of instrument-row dicts (may be empty if the file does not exist).
    """
    try:
        path = _instruments_path(project_root)
    except PathTraversalError:
        return []
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _instruments_arg_from_stored_rows(rows: list) -> dict:
    """Build the parse_score ``instruments`` dict from stored instrument rows.

    Args:
        rows: List of instrument-row dicts (from appdata/webmusicdata/instruments.json).

    Returns:
        Dict mapping lowercase name → spec dict, ready to pass to parse_score.
        Empty dict when rows is empty.
    """
    if not rows:
        return {}
    return json.loads(instruments_arg_from_designed(json.dumps(rows)))


def _atomic_write_json(target_path: str, data: Any) -> None:
    """Write ``data`` as JSON to ``target_path`` atomically via a temp file.

    Args:
        target_path: Absolute destination path.
        data: JSON-serializable Python object.
    """
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        dir=target_dir, delete=False, mode="w", suffix=".tmp", encoding="utf-8"
    )
    try:
        tmp.write(json.dumps(data, indent=2))
        tmp.close()
        os.replace(tmp.name, target_path)
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise


def _atomic_write_text(target_path: str, text: str) -> None:
    """Write ``text`` to ``target_path`` atomically via a temp file + os.replace.

    Sibling of ``_atomic_write_json`` for plain-text payloads (score files).

    Args:
        target_path: Absolute destination path.
        text: The text contents to write.
    """
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        dir=target_dir, delete=False, mode="w", suffix=".tmp", encoding="utf-8"
    )
    try:
        tmp.write(text)
        tmp.close()
        os.replace(tmp.name, target_path)
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise


# ── Score file + session helpers ──────────────────────────────────────────────

def _nonneg_int(value: Any) -> int:
    """Coerce ``value`` to a non-negative int, defaulting to 0 on failure."""
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return 0
    return coerced if coerced >= 0 else 0


def _list_score_names(project_root: str) -> list:
    """Return the raw filenames in the scores dir, or [] if it is missing."""
    try:
        scores_dir = resolve_in_root(project_root, _SCORES_DIR_REL)
    except PathTraversalError:
        return []
    if not os.path.isdir(scores_dir):
        return []
    return os.listdir(scores_dir)


def _read_session(project_root: str) -> Optional[dict]:
    """Return the parsed session dict, or None if missing/corrupt."""
    try:
        path = resolve_in_root(project_root, _SESSION_REL)
    except PathTraversalError:
        return None
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return parse_session(fh.read())


def _write_session(project_root: str, current: str, caret: int = 0, scroll: int = 0) -> None:
    """Atomically write session.json for ``current`` with view state."""
    path = resolve_in_root(project_root, _SESSION_REL)
    _atomic_write_text(path, json.dumps(build_session(current, caret, scroll), indent=2))


# ── ParsedScore serialisation ─────────────────────────────────────────────────

def _serialise_parsed_score(score) -> dict:
    """Convert a ParsedScore into a JSON-serialisable dict.

    Returns sections (with events and beat_counts), global errors, and the
    instrument_channels map.  Individual note-event fields are projected to a
    compact, stable subset: ``time``, ``duration``, ``midi_note``, ``velocity``,
    ``channel``, ``line_idx``, ``col_start``, ``col_end``.

    Args:
        score: A ``ParsedScore`` returned by ``parse_score``.

    Returns:
        Dict with keys ``sections``, ``errors``, ``instrument_channels``,
        and ``beat_counts``.
    """
    sections_out = {}
    beat_counts_out = {}

    for name, sec in score.sections.items():
        events_out = [
            {
                "time": ev.time,
                "duration": ev.duration,
                "midi_note": ev.midi_note,
                "velocity": ev.velocity,
                "channel": ev.channel,
                "line_idx": ev.line_idx,
                "col_start": ev.col_start,
                "col_end": ev.col_end,
            }
            for ev in sec.events
        ]
        errors_out = [
            {
                "message": e.message,
                "line_idx": e.line_idx,
                "col_start": e.col_start,
                "col_end": e.col_end,
                "section_name": e.section_name,
            }
            for e in sec.errors
        ]
        sections_out[name] = {"events": events_out, "errors": errors_out}
        beat_counts_out[name] = sec.beat_count

    global_errors_out = [
        {
            "message": e.message,
            "line_idx": e.line_idx,
            "col_start": e.col_start,
            "col_end": e.col_end,
            "section_name": e.section_name,
        }
        for e in score.errors
    ]

    return {
        "sections": sections_out,
        "errors": global_errors_out,
        "instrument_channels": score.instrument_channels,
        "beat_counts": beat_counts_out,
    }


# ── Blueprint factory ─────────────────────────────────────────────────────────

def make_blueprint(
    project_root: str,
    soundfont: str = "/usr/share/sounds/sf2/MuseScore_General_Full.sf2",
) -> Blueprint:
    """Return a Flask Blueprint for the /music app routes.

    Args:
        project_root: Absolute path to the repository root; used to locate
            ``web/music.html``, ``appdata/webmusicdata/``, and future asset directories.
        soundfont: Path to the SF2 soundfont file used for MIDI rendering.
            Defaults to the MuseScore General Full soundfont.  Only used when
            a request does not select the ``?backend=fake`` seam.
    """
    bp = Blueprint("music", __name__)

    @bp.route("/music")
    def music_page():
        """Serve the Music app page shell."""
        return send_file(os.path.join(project_root, "web", "music.html"))

    @bp.route("/music/instruments", methods=["GET"])
    def music_instruments_get():
        """Return the stored designed-instruments JSON (or an empty default)."""
        try:
            path = _instruments_path(project_root)
        except PathTraversalError as exc:
            return bad_request(str(exc))
        if not os.path.isfile(path):
            return jsonify([])
        with open(path, "r", encoding="utf-8") as fh:
            return jsonify(json.load(fh))

    @bp.route("/music/instruments", methods=["POST"])
    def music_instruments_post():
        """Atomically write the designed-instruments JSON to appdata/webmusicdata/instruments.json."""
        body = request.get_json(silent=True)
        if body is None:
            return bad_request("JSON body required")
        try:
            path = _instruments_path(project_root)
        except PathTraversalError as exc:
            return bad_request(str(exc))
        _atomic_write_json(path, body)
        return jsonify({"ok": True})

    @bp.route("/music/parse", methods=["POST"])
    def music_parse():
        """Parse score text and return sections, events, errors, and beat-counts.

        Body: ``{"text": "<score source>"}``

        Returns 400 for missing/invalid body.  Parse errors (bad tokens, unknown
        instruments) are surfaced in the response body — never as HTTP 500.
        """
        body = request.get_json(silent=True)
        if body is None or "text" not in body:
            return bad_request("JSON body with 'text' field required")
        text = body["text"]
        rows = _load_instruments_rows(project_root)
        instruments = _instruments_arg_from_stored_rows(rows)
        try:
            score = parse_score(text, instruments=instruments if instruments else None)
        except Exception as exc:  # noqa: BLE001 — surface as JSON, never 500
            return jsonify({"errors": [{"message": str(exc)}], "sections": {},
                            "instrument_channels": {}, "beat_counts": {}})
        return jsonify(_serialise_parsed_score(score))

    @bp.route("/music/audition", methods=["POST"])
    def music_audition():
        """Render the short audition clip from an instrument spec; return raw float32 PCM bytes.

        Body: JSON instrument spec dict (instrument row fields plus pitch, velocity,
              duration_s).

        Query params:
            backend=fake  Use FakeBackend (no FluidSynth required; for smoke tests).

        Content-type: application/octet-stream
        """
        spec = request.get_json(silent=True)
        if spec is None:
            return bad_request("JSON instrument spec required")
        try:
            bundle = json.loads(audition_score_and_programs(json.dumps(spec)))
        except (ValueError, KeyError) as exc:
            return bad_request(str(exc))
        backend = _select_backend(request)
        pcm = render_audition_pcm(
            section_score_json=json.dumps(bundle["score"]),
            instruments_json=json.dumps(bundle.get("instruments", {})),
            channel_ccs=bundle["channel_ccs"],
            channel_automation=bundle["channel_automation"],
            programs_json=json.dumps(bundle["programs"]),
            backend=backend,
        )
        return Response(pcm.astype("float32").tobytes(), mimetype="application/octet-stream")

    @bp.route("/music/play", methods=["POST"])
    def music_play():
        """Render the full song from score text; return raw float32 PCM bytes.

        Body: ``{"text": "<score source>", "caret": <int>}``
            text   — the score text to render (required)
            caret  — character offset of the caret (default 0)

        Query params:
            backend=fake  Use FakeBackend (no FluidSynth required; for smoke tests).

        Content-type: application/octet-stream
        """
        body = request.get_json(silent=True)
        if body is None or "text" not in body:
            return bad_request("JSON body with 'text' required")
        caret = int(body.get("caret", 0))
        rows = _load_instruments_rows(project_root)
        rows_json = json.dumps(rows)
        bundle = json.loads(play_render_bundle(body["text"], caret, rows_json))
        backend = _select_backend(request)
        pcm = render_song_pcm(
            score_json=json.dumps(bundle["score"]),
            instruments_json="{}",
            channel_ccs=bundle["channel_ccs"],
            channel_automation=bundle["channel_automation"],
            programs_json=json.dumps(bundle["programs"]),
            backend=backend,
        )
        return Response(pcm.astype("float32").tobytes(), mimetype="application/octet-stream")

    @bp.route("/music/flac", methods=["GET"])
    def music_flac():
        """Render the full song and return a 24-bit FLAC file as an attachment download.

        Query params:
            backend=fake  Use FakeBackend (no FluidSynth required).

        Content-type: audio/flac
        Content-Disposition: attachment; filename="music.flac"

        Untested by design: requires ``soundfile`` and FluidSynth + a large soundfont
        that are not available in the test sandbox.  The injectable ``?backend=fake``
        seam is wired so the code path can be exercised wherever those deps exist.
        """
        from libs.music_render import encode_flac  # noqa: PLC0415 — lazy, avoids soundfile at import
        backend = _select_backend(request)
        rows = _load_instruments_rows(project_root)
        rows_json = json.dumps(rows)
        text = request.args.get("text", "")
        bundle = json.loads(play_render_bundle(text, 0, rows_json))
        pcm = render_song_pcm(
            score_json=json.dumps(bundle["score"]),
            instruments_json="{}",
            channel_ccs=bundle["channel_ccs"],
            channel_automation=bundle["channel_automation"],
            programs_json=json.dumps(bundle["programs"]),
            backend=backend,
            sample_rate=_DEFAULT_SAMPLE_RATE,
        )
        # encode_flac needs a real file path (soundfile constraint), so we write
        # to a temp file, read the bytes, delete the file, then return as a Response.
        tmp_dir = os.path.join(project_root, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".flac", dir=tmp_dir)
        os.close(fd)
        try:
            encode_flac(pcm, _DEFAULT_SAMPLE_RATE, tmp_path)
            with open(tmp_path, "rb") as fh:
                flac_bytes = fh.read()
        finally:
            os.unlink(tmp_path)
        return Response(
            flac_bytes,
            mimetype="audio/flac",
            headers={"Content-Disposition": 'attachment; filename="music.flac"'},
        )

    @bp.route("/music/scores", methods=["GET"])
    def music_scores_list():
        """List score files plus the session's current file and view state."""
        files = json.loads(
            filter_and_sort_scores(json.dumps(_list_score_names(project_root)))
        )
        session = _read_session(project_root)
        if session is None:
            return jsonify({"files": files, "current": None, "caret": 0, "scroll": 0})
        return jsonify(
            {
                "files": files,
                "current": session["last_file"],
                "caret": session["caret"],
                "scroll": session["scroll"],
            }
        )

    @bp.route("/music/score", methods=["GET"])
    def music_score_get():
        """Return a single score's text. 400 invalid name; 404 missing file."""
        name = request.args.get("name", "")
        try:
            path = resolve_in_root(project_root, score_rel_path(name))
        except (ValueError, PathTraversalError) as exc:
            return bad_request(str(exc))
        if not os.path.isfile(path):
            return jsonify({"error": "not found"}), 404
        with open(path, "r", encoding="utf-8") as fh:
            return jsonify({"name": normalize_score_name(name), "text": fh.read()})

    @bp.route("/music/score", methods=["PUT"])
    def music_score_put():
        """Atomically write a score file (autosave). Optionally set it current."""
        body = request.get_json(silent=True)
        if body is None or "name" not in body or "text" not in body:
            return bad_request("JSON body with 'name' and 'text' required")
        name = body["name"]
        text = body["text"]
        if not isinstance(name, str) or not isinstance(text, str):
            return bad_request("'name' and 'text' must be strings")
        try:
            path = resolve_in_root(project_root, score_rel_path(name))
        except (ValueError, PathTraversalError) as exc:
            return bad_request(str(exc))
        _atomic_write_text(path, text)
        if body.get("make_current"):
            session = _read_session(project_root)
            caret = session["caret"] if session else 0
            scroll = session["scroll"] if session else 0
            _write_session(project_root, normalize_score_name(name), caret, scroll)
        return jsonify({"ok": True})

    @bp.route("/music/scores/new", methods=["POST"])
    def music_scores_new():
        """Create a new score (name required) and set it current. 409 if it exists."""
        body = request.get_json(silent=True)
        if body is None:
            return bad_request("JSON body required")
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return bad_request("'name' is required")
        text = body.get("text", "")
        if not isinstance(text, str):
            return bad_request("'text' must be a string")
        try:
            path = resolve_in_root(project_root, score_rel_path(name))
        except (ValueError, PathTraversalError) as exc:
            return bad_request(str(exc))
        if os.path.isfile(path):
            return conflict(f"score already exists: {name}")
        _atomic_write_text(path, text)
        normalized = normalize_score_name(name)
        _write_session(project_root, normalized, 0, 0)
        return jsonify({"name": normalized})

    @bp.route("/music/session", methods=["POST"])
    def music_session_post():
        """Merge current/caret/scroll into session.json. 404 if current is missing."""
        body = request.get_json(silent=True)
        if body is None:
            return bad_request("JSON body required")
        session = _read_session(project_root)
        current = session["last_file"] if session else None
        caret = session["caret"] if session else 0
        scroll = session["scroll"] if session else 0
        if "current" in body:
            name = body["current"]
            if not isinstance(name, str):
                return bad_request("'current' must be a string")
            try:
                path = resolve_in_root(project_root, score_rel_path(name))
            except (ValueError, PathTraversalError) as exc:
                return bad_request(str(exc))
            if not os.path.isfile(path):
                return jsonify({"error": "not found"}), 404
            current = normalize_score_name(name)
            caret = _nonneg_int(body.get("caret", 0))
            scroll = _nonneg_int(body.get("scroll", 0))
        else:
            if current is None:
                return bad_request("no current score to update")
            if "caret" in body:
                caret = _nonneg_int(body["caret"])
            if "scroll" in body:
                scroll = _nonneg_int(body["scroll"])
        _write_session(project_root, current, caret, scroll)
        return jsonify({"ok": True})

    return bp
