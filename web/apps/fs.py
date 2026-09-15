"""Blueprint for filesystem routes: /, /fs/read, /fs/write, /fs/list, /fs/mkdir."""

import os

from flask import Blueprint, request, jsonify, Response
from libs.web_runtime import resolve_in_root, PathTraversalError
from web.apps._common import safe_path, bad_request, conflict, entry_info


def make_blueprint(project_root: str) -> Blueprint:
    bp = Blueprint("fs", __name__)

    @bp.route("/fs/read")
    def fs_read():
        """Return the file's contents as plain text."""
        rel = request.args.get("path")
        if not rel:
            return bad_request("path param required")
        try:
            target = safe_path(project_root, rel)
        except PathTraversalError as e:
            return bad_request(str(e))
        if not os.path.isfile(target):
            return jsonify({"error": "not a file"}), 404
        with open(target, encoding="utf-8") as f:
            text = f.read()
        return Response(text, mimetype="text/plain")

    @bp.route("/fs/write", methods=["POST"])
    def fs_write():
        """Write a file, creating parent dirs; noOverwrite:true fails if path exists."""
        data = request.get_json(force=True) or {}
        rel = data.get("path")
        contents = data.get("contents")
        if not rel or contents is None:
            return bad_request("path and contents required")
        try:
            target = safe_path(project_root, rel)
        except PathTraversalError as e:
            return bad_request(str(e))
        if data.get("noOverwrite") and os.path.exists(target):
            return conflict("path exists")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(contents)
        return jsonify({"ok": True, "path": target})

    @bp.route("/fs/list")
    def fs_list():
        """Return a directory listing with name, isDir, and size for each entry."""
        rel = request.args.get("path")
        if not rel:
            return bad_request("path param required")
        try:
            target = safe_path(project_root, rel)
        except PathTraversalError as e:
            return bad_request(str(e))
        if not os.path.isdir(target):
            return jsonify({"error": "not a directory"}), 404
        return jsonify([entry_info(target, n) for n in sorted(os.listdir(target))])

    @bp.route("/fs/mkdir", methods=["POST"])
    def fs_mkdir():
        """Create a directory (parents ok)."""
        data = request.get_json(force=True) or {}
        rel = data.get("path")
        if not rel:
            return bad_request("path param required")
        try:
            target = safe_path(project_root, rel)
        except PathTraversalError as e:
            return bad_request(str(e))
        os.makedirs(target, exist_ok=True)
        return jsonify({"ok": True, "path": target})

    return bp
