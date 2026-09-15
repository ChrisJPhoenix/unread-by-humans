"""Canonical exception→report assembly, storm gate, and crash envelope.

Converts live exceptions or manually supplied parts into a normalized report
dict suitable for logging, display, or transmission.  All public functions are
boring (effect-free and deterministic given fixed inputs), except that
`report_from_parts` defaults `time` to the current UTC instant when the
caller omits it — callers that need deterministic output should always supply
an explicit `time`.

Deferred to V2: field redaction, log-write concurrency helpers.
"""
import datetime
import traceback
from typing import Union


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_stack(
    stack: Union[str, list, None],
) -> Union[str, list]:
    """Return stack unchanged; accept str, list-of-frames, or None (→ empty str)."""
    if stack is None:
        return ""
    return stack


def _stack_lines(stack: Union[str, list]) -> list:
    """Return a flat list of 'file:line' strings from either stack form."""
    if isinstance(stack, str):
        return [ln for ln in stack.splitlines() if ln.strip()]
    # list of {"file": ..., "line": ...} dicts
    return [f"{frame.get('file', '')}:{frame.get('line', '')}" for frame in stack]


def _current_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def report_from_parts(
    type_name: str,
    message: str,
    stack: Union[str, list, None] = "",
    path: str = None,
    payload: str = None,
    url: str = None,
    time: str = None,
) -> dict:
    """Assemble a canonical report dict from individually supplied parts.

    Args:
        type_name: Exception class name (or any short category label).
        message: Human-readable description of the problem.
        stack: Either a newline-delimited string of 'file:line' frames OR a
            list of ``{"file": str, "line": int}`` dicts.  Pass ``""`` or
            ``None`` when there is no stack.
        path: Optional request path at which the error occurred.
        payload: Optional serialised request payload (string form).
        url: Optional full URL associated with the error.
        time: Optional ISO-8601 UTC timestamp string.  When ``None`` the
            current UTC instant is used (makes the report non-deterministic
            unless the caller supplies an explicit value).

    Returns:
        Dict with at minimum ``{"type", "message", "stack"}``, plus whichever
        of ``path``/``payload``/``url``/``time`` were provided or defaulted.
    """
    report: dict = {
        "type": type_name,
        "message": message,
        "stack": _normalise_stack(stack),
        "time": time if time is not None else _current_utc_iso(),
    }
    if path is not None:
        report["path"] = path
    if payload is not None:
        report["payload"] = payload
    if url is not None:
        report["url"] = url
    return report


def build_report(
    exc: BaseException,
    *,
    path: str = None,
    payload: str = None,
    url: str = None,
) -> dict:
    """Build a canonical report dict from a live exception.

    Extracts ``type`` (class name), ``message`` (``str(exc)``), and a
    structured ``stack`` (list of ``{"file", "line"}`` dicts derived from
    ``traceback.extract_tb(exc.__traceback__)``) then delegates to
    :func:`report_from_parts`.

    Args:
        exc: A caught exception, ideally still carrying ``__traceback__``.
        path: Forwarded to :func:`report_from_parts`.
        payload: Forwarded to :func:`report_from_parts`.
        url: Forwarded to :func:`report_from_parts`.

    Returns:
        Canonical report dict (same shape as :func:`report_from_parts`).
    """
    frames = [
        {"file": frame.filename, "line": frame.lineno}
        for frame in traceback.extract_tb(exc.__traceback__)
    ]
    return report_from_parts(
        type_name=type(exc).__name__,
        message=str(exc),
        stack=frames,
        path=path,
        payload=payload,
        url=url,
    )


def format_report(report: dict) -> str:
    """Render a report as a copy-paste code-fence block.

    Handles both string and list-of-frames ``stack`` forms.  The output is
    always wrapped in triple-backtick fences so it can be pasted directly
    into a chat message or log entry.

    Args:
        report: A dict produced by :func:`report_from_parts` or
            :func:`build_report`.

    Returns:
        A string that begins and ends with ````` ``` `````.
    """
    lines = []

    lines.append(f"{report.get('type', 'Error')}: {report.get('message', '')}")

    stack = report.get("stack", "")
    stack_lines = _stack_lines(stack)
    if stack_lines:
        lines.append("Stack:")
        for sl in stack_lines:
            lines.append(f"  {sl}")

    if "path" in report:
        lines.append(f"Path: {report['path']}")
    if "payload" in report:
        lines.append(f"Payload: {report['payload']}")
    if "url" in report:
        lines.append(f"URL: {report['url']}")
    if "time" in report:
        lines.append(f"Time: {report['time']}")

    body = "\n".join(lines)
    return f"```\n{body}\n```"


def storm_key(report: dict) -> str:
    """Compute a deduplication fingerprint for the report.

    The key is formed from ``type`` concatenated with each stack frame's
    ``file:line`` (or each non-empty line of a string stack), joined by ``|``.
    The ``message`` is intentionally excluded so that the same error with
    different dynamic text (e.g. a varying user id) hashes to the same key.

    Args:
        report: A dict produced by :func:`report_from_parts` or
            :func:`build_report`.

    Returns:
        A plain string fingerprint.
    """
    parts = [report.get("type", "")]
    parts.extend(_stack_lines(report.get("stack", "")))
    return "|".join(parts)


def should_emit(
    key: str,
    seen_keys: Union[set, frozenset, list, str],
    distinct_count: int,
    cap: int = 10,
) -> bool:
    """Decide whether a report with the given key should be emitted.

    Returns ``False`` when the key is a duplicate (already in *seen_keys*) or
    when the storm cap has been reached (``distinct_count >= cap``); otherwise
    returns ``True``.

    The caller is responsible for updating *seen_keys* and *distinct_count*
    after a ``True`` return — this function has no side-effects.

    *seen_keys* may be a set, frozenset, list, or a ``str``.  When it is a
    string it is treated as a comma-separated list of already-seen fingerprint
    keys; an empty string means the set is empty.  This string form lets the
    test harness supply seen-keys as a plain quoted arg.

    Args:
        key: Fingerprint from :func:`storm_key`.
        seen_keys: Collection of already-emitted fingerprints.  Accepts
            set / frozenset / list, or a comma-separated string.
        distinct_count: Number of distinct keys emitted so far.
        cap: Maximum number of distinct keys before storm suppression kicks in.
            Defaults to ``10``.

    Returns:
        ``True`` if the report should be forwarded; ``False`` to suppress it.
    """
    if isinstance(seen_keys, str):
        seen_keys = [k.strip() for k in seen_keys.split(",") if k.strip()]
    if key in seen_keys:
        return False
    if distinct_count >= cap:
        return False
    return True


def crash_envelope(report: dict) -> dict:
    """Wrap a report in the reserved crash-key envelope.

    The ``__crash__`` key is reserved by the error-handler and
    ``run_guarded``; no application card should ever write it directly.

    Args:
        report: A canonical report dict.

    Returns:
        ``{"__crash__": report}``
    """
    return {"__crash__": report}


def unwrap_envelope(envelope: dict) -> dict:
    """Extract the report from a crash envelope produced by :func:`crash_envelope`.

    This is the inverse of :func:`crash_envelope` and exists primarily so the
    test harness can exercise the envelope round-trip without dict-indexing
    syntax (``$ref["__crash__"]`` is not a valid harness value form).

    Args:
        envelope: A dict of the form ``{"__crash__": report}``.

    Returns:
        The inner report dict.

    Raises:
        KeyError: If *envelope* does not contain the ``__crash__`` key.
    """
    return envelope["__crash__"]
