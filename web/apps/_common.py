"""Shared web-app route helpers used across per-app blueprint modules."""

import os

from flask import jsonify
from datetime import datetime, timezone
from libs.web_runtime import resolve_in_root


def safe_path(project_root: str, rel: str):
    return resolve_in_root(project_root, rel)


def bad_request(msg):
    return jsonify({"error": msg}), 400


def conflict(msg):
    return jsonify({"error": msg}), 409


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ms-precision Z string matching creation_stack._iso_to_ms format."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def usage_total(attempts):
    """Sum the four token buckets across all attempts for the log (defensive)."""
    total = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_write_tokens": 0,
             "thinking_tokens": 0}
    for a in attempts:
        u = a.get("usage") or {}
        for k in total:
            total[k] += u.get(k, 0)
    return total


def entry_info(dir_path, name):
    p = os.path.join(dir_path, name)
    return {"name": name, "isDir": os.path.isdir(p), "size": os.lstat(p).st_size}
