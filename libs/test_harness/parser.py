"""Plan-file parser.

Plan-file grammar (see design/testing.md for the canonical
spec):

    file        := stanza ( blank+ stanza )*
    stanza      := comment* call-line field*
    call-line   := 'call:' WS dotted-name
    field       := arg-field | save-field | expect-field | normalize-field | tolerance-field | comment
    arg-field   := 'arg' WS name WS '=' WS value-or-block
    save-field  := 'save' WS 'as:' WS name
    expect-field:= 'expect' WS name WS '=' WS value-or-block
    normalize-field := 'normalize:' WS pattern WS '=>' WS replacement
    tolerance-field := 'tolerance:' WS float
    comment     := '#' .* EOL

Values are scalars (int, float, bool, none, "string", $var) or
indented 2-D numeric blocks (array rows are space-separated
numbers). String escapes are \\n \\t \\" \\\\ plus the general
\\xHH hex-byte escape.
"""
import string
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# Sentinel marking a "$var" reference whose dereference happens
# at run-time, not parse-time.
@dataclass
class VarRef:
    name: str


@dataclass
class Step:
    call: str  # "lib.fn"
    args: dict[str, Any] = field(default_factory=dict)
    expects: dict[str, Any] = field(default_factory=dict)
    expect_masks: dict[str, Any] = field(default_factory=dict)
    expect_zero_scales: dict[str, Any] = field(default_factory=dict)
    save_as: str | None = None
    normalize: list[tuple[str, str]] = field(default_factory=list)
    tolerance: float | None = None
    leading_comments: list[str] = field(default_factory=list)
    # For learn-mode re-emission: the raw line numbers covered.
    line_start: int = 0
    line_end: int = 0


class ParseError(Exception):
    def __init__(self, line_no: int, message: str):
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


def parse(text: str) -> list[Step]:
    """Parse `text` into a list of Steps.

    `text` is the full contents of a plan file (or an embedded
    plan string for the self-test).
    """
    lines = text.splitlines()
    # Walk lines, accumulating stanzas. A stanza ends at a blank
    # line or EOF. Leading comments belong to the next stanza.
    steps: list[Step] = []
    i = 0
    n = len(lines)
    pending_comments: list[str] = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == "":
            i += 1
            continue
        if stripped.startswith("#"):
            pending_comments.append(stripped)
            i += 1
            continue

        # Start of a stanza
        stanza_start = i
        if not stripped.startswith("call:"):
            raise ParseError(
                i + 1,
                "stanza must begin with 'call:' "
                f"(got {stripped!r})",
            )
        call = stripped[len("call:"):].strip()
        if not call:
            raise ParseError(i + 1, "'call:' missing function name")

        step = Step(
            call=call,
            leading_comments=pending_comments,
            line_start=stanza_start,
        )
        pending_comments = []
        i += 1

        # Consume field lines until blank or EOF
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if stripped == "":
                break
            if stripped.startswith("#"):
                # Comment inside a stanza — attach to leading_comments
                # of next stanza by buffering it after stanza ends.
                # Simpler: ignore for now (we round-trip stanza
                # comments separately).
                i += 1
                continue
            if stripped.startswith("call:"):
                # Adjacent call with no blank separator — treat as
                # error to keep stanza boundaries clear.
                raise ParseError(
                    i + 1,
                    "second 'call:' without blank-line separator",
                )

            consumed = _parse_field(lines, i, step)
            i = consumed

        step.line_end = i
        steps.append(step)

    return steps


def _parse_field(lines: list[str], i: int, step: Step) -> int:
    """Parse one field starting at lines[i]; return the next i."""
    line = lines[i]
    stripped = line.strip()

    if stripped.startswith("save as:"):
        var = stripped[len("save as:"):].strip()
        if not var:
            raise ParseError(i + 1, "'save as:' missing variable name")
        step.save_as = var
        return i + 1

    if stripped.startswith("normalize:"):
        rest = stripped[len("normalize:"):].strip()
        if "=>" not in rest:
            raise ParseError(i + 1, "'normalize:' missing '=>' separator")
        pattern, _, repl = rest.partition("=>")
        pattern = pattern.strip()
        repl = repl.strip()
        if pattern == "":
            raise ParseError(i + 1, "'normalize:' missing pattern")
        step.normalize.append((pattern, repl))
        return i + 1

    if stripped.startswith("tolerance:"):
        rest = stripped[len("tolerance:"):].strip()
        if rest == "":
            raise ParseError(i + 1, "'tolerance:' missing value")
        try:
            value = float(rest)
        except ValueError:
            raise ParseError(i + 1, f"'tolerance:' invalid value: {rest!r}")
        step.tolerance = value
        return i + 1

    for prefix in ("arg ", "expect "):
        if stripped.startswith(prefix):
            kind = prefix.strip()  # "arg" or "expect"
            rest = stripped[len(prefix):]
            if "=" not in rest:
                raise ParseError(
                    i + 1, f"'{kind}' line missing '='"
                )
            name, _, raw_value = rest.partition("=")
            name = name.strip()
            raw_value = raw_value.strip()
            if not name:
                raise ParseError(i + 1, f"'{kind}' missing field name")

            if raw_value == "":
                # Block value: collect indented continuation lines.
                value, mask, zero_scales, next_i = _parse_block(lines, i + 1)
            else:
                value = _parse_scalar(raw_value, i + 1)
                mask = None
                zero_scales = None
                next_i = i + 1
                if isinstance(value, float):
                    zero_scales = _zero_target_scale(raw_value) if value == 0 else 0.0

            if kind == "arg":
                step.args[name] = value
            else:
                step.expects[name] = value
                if mask is not None:
                    step.expect_masks[name] = mask
                if zero_scales is not None:
                    step.expect_zero_scales[name] = zero_scales
            return next_i

    raise ParseError(
        i + 1,
        f"unrecognized field line {stripped!r}",
    )


def _parse_block(lines: list[str], i: int) -> tuple[Any, Any, Any, int]:
    """Collect indented lines starting at `i` into a 2-D array.

    Returns (arr, float_spelled_mask, zero_scales, next_index). The block
    ends at the first line that is not indented (relative to column 0) —
    blank lines and comments inside the block are not currently supported.
    float_spelled_mask is a bool ndarray of the same shape as arr,
    True where the token was float-spelled (has a decimal point or
    exponent). zero_scales is a float64 ndarray of the same shape, holding
    the digit-derived surrogate magnitude for float-spelled zero tokens
    (nonzero for zero targets, 0.0 otherwise).
    """
    rows: list[list[float]] = []
    float_spelled_rows: list[list[bool]] = []
    zero_scale_rows: list[list[float]] = []
    while i < len(lines):
        line = lines[i]
        if line == "" or not line[0].isspace():
            break
        stripped = line.strip()
        if stripped == "":
            break
        if stripped.startswith("#"):
            i += 1
            continue
        tokens = stripped.split()
        values: list[float] = []
        spelled: list[bool] = []
        zero_scales: list[float] = []
        try:
            for t in tokens:
                v, is_float = _parse_number(t)
                values.append(v)
                spelled.append(is_float)
                zero_scales.append(_zero_target_scale(t) if is_float and v == 0 else 0.0)
        except ValueError as e:
            raise ParseError(i + 1, str(e))
        rows.append(values)
        float_spelled_rows.append(spelled)
        zero_scale_rows.append(zero_scales)
        i += 1

    if not rows:
        raise ParseError(i + 1, "empty block after '='")

    width = len(rows[0])
    if not all(len(r) == width for r in rows):
        raise ParseError(i + 1, "block rows have inconsistent widths")

    # Choose dtype: int if every value is integral, else float.
    if all(float(v).is_integer() for r in rows for v in r):
        arr = np.array(rows, dtype=np.int64)
    else:
        arr = np.array(rows, dtype=np.float64)
    mask = np.array(float_spelled_rows, dtype=bool)
    zero_scales_arr = np.array(zero_scale_rows, dtype=np.float64)
    return arr, mask, zero_scales_arr, i


def _parse_number(token: str) -> tuple[float, bool]:
    """Parse a numeric token. Returns (value, is_float_spelled).
    A token is float-spelled when it is not parseable as an int
    (i.e. it has a decimal point or exponent)."""
    try:
        return int(token), False
    except ValueError:
        pass
    try:
        return float(token), True
    except ValueError as e:
        raise ValueError(f"not a number: {token!r}") from e


def _zero_target_scale(token: str) -> float:
    """Surrogate magnitude for a float-spelled zero target, derived from its
    written decimal places: `0.0` -> 1.0, `0.00` -> 0.1, `0.000` -> 0.01
    (10 ** (1 - d), d = count of fractional digits). The band
    `tolerance * scale` then tightens 10x per extra written zero. A bare
    `0.` (d = 0) gives 10.0."""
    mantissa = token.split("e")[0].split("E")[0]
    fractional_digits = len(mantissa.split(".")[1]) if "." in mantissa else 0
    return 10.0 ** (1 - fractional_digits)


def _parse_scalar(raw: str, line_no: int) -> Any:
    if raw.startswith("$"):
        name = raw[1:].strip()
        if not name:
            raise ParseError(line_no, "'$' must be followed by a var name")
        return VarRef(name)
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.lower() in ("null", "none"):
        return None
    if raw.startswith('"'):
        if not raw.endswith('"') or len(raw) < 2:
            raise ParseError(line_no, "unterminated string literal")
        return _unescape(raw[1:-1], line_no)
    # Number?
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    raise ParseError(line_no, f"unparseable value: {raw!r}")


# Named escapes recognized inside a quoted string literal. Any other
# escaped character (besides "x", handled separately below) keeps its
# own letter and drops the backslash.
_NAMED_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _decode_hex_escape(s: str, i: int, line_no: int) -> tuple[str, int]:
    """Decode a `\\xHH` escape whose backslash is at `s[i]`.

    Returns (decoded_char, next_index). Raises ParseError if the two
    characters following `x` are not both hex digits (including the
    case where the string ends before two digits appear) — a plan
    file cannot silently mean the wrong byte.
    """
    digits = s[i + 2:i + 4]
    if len(digits) < 2 or not all(c in string.hexdigits for c in digits):
        raise ParseError(
            line_no,
            f"'\\x' must be followed by two hex digits (got {digits!r})",
        )
    return chr(int(digits, 16)), i + 4


def _unescape(s: str, line_no: int) -> str:
    """Decode the backslash escapes in a quoted plan-file string.

    Recognized escapes: \\n, \\t, \\", \\\\, and the general \\xHH
    hex-byte escape (e.g. \\x0d for a bare CR). \\xHH exists because a
    control byte like CR cannot be written literally in a plan file —
    `Path.read_text()`'s universal-newline translation would eat it —
    so its two-hex-digit spelling is the only way to express it. Any
    other escaped character keeps its own letter, dropping the
    backslash.
    """
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "x":
                decoded, i = _decode_hex_escape(s, i, line_no)
                out.append(decoded)
                continue
            out.append(_NAMED_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)
