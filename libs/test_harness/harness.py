"""Test harness — plan execution, mismatch detection, learn mode.

Public entry points used by the CLI:

    run_plan_file(path, learn=False) -> PlanResult
    run_text(text, plan_path=None) -> PlanResult
    discover_plans(libs_dir, lib_filter=None, mode="quick")
        -> list[Path]
    check_stub_coverage(libs_dir) -> CoverageReport

The harness pins `seed_registry` to a fixed master seed at the
start of every plan so determinism doesn't depend on the order
plans are run.
"""
from __future__ import annotations

import difflib
import importlib
import io
import os
import re
import sys
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from libs.test_harness import parser
from libs.test_harness.parser import Step, VarRef
from libs.test_harness.stub import is_stub
from libs import seed_registry

# Master seed used by `pin()` at the start of every plan. Stable
# across runs by design — the harness is supposed to be
# deterministic. Per-library / per-test seeds derive from this.
HARNESS_MASTER_SEED = 0xC0FFEE
DEFAULT_TOLERANCE = 1e-6


@dataclass
class StepResult:
    step: Step
    status: str  # "pass" | "fail" | "stubbed" | "error"
    actual: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    stub_target: str = ""  # "lib:fn" if status=="stubbed"


@dataclass
class PlanResult:
    path: str
    steps: list[StepResult] = field(default_factory=list)
    status: str = "pass"  # "pass" | "fail" | "error"
    stubbed_calls: list[str] = field(default_factory=list)

    def short(self) -> str:
        return f"{self.status:8} {self.path}"


# --- Discovery ---------------------------------------------------------


def plan_glob_for_mode(mode: str) -> str:
    """The plan-filename glob one mode selects inside a library directory.

    `quick` is the tier `bin/green` gates on, so it takes every
    `test_primary*.txt`: a library whose plan has been split per feature
    keeps all of its quick coverage by naming each piece
    `test_primary_<feature>.txt`. `full` takes every `test_*.txt`, which
    additionally picks up scenario and stub plans.

    Raises:
        ValueError: `mode` is neither "quick" nor "full".
    """
    if mode == "quick":
        return "test_primary*.txt"
    if mode == "full":
        return "test_*.txt"
    raise ValueError(f"unknown mode: {mode}")


def discover_plans(
    libs_dir: str | Path,
    lib_filter: list[str] | None = None,
    mode: str = "quick",
) -> list[Path]:
    """Find plan files under libs_dir.

    Modes:
      - "quick": every `test_primary*.txt` in each library.
      - "full":  every `test_*.txt` file in each library.
    """
    libs_dir = Path(libs_dir)
    plan_glob = plan_glob_for_mode(mode)
    plans: list[Path] = []
    for child in sorted(libs_dir.iterdir()):
        if not child.is_dir():
            continue
        if lib_filter and child.name not in lib_filter:
            continue
        for p in sorted(child.glob(plan_glob)):
            plans.append(p)
    return plans


# --- Stub coverage check ----------------------------------------------


@dataclass
class CoverageReport:
    orphan_stubs: list[str] = field(default_factory=list)
    orphan_plans: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.orphan_stubs and not self.orphan_plans


def check_stub_coverage(libs_dir: str | Path) -> CoverageReport:
    """Verify every @stub function has a matching test_stub_<fn>.txt
    and every test_stub_<fn>.txt names a real @stub function."""
    libs_dir = Path(libs_dir)
    report = CoverageReport()
    for child in sorted(libs_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name in ("cli", "__pycache__"):
            continue
        lib_name = child.name
        try:
            mod = importlib.import_module(f"libs.{lib_name}")
        except Exception:
            # Library may not be importable yet (e.g., scaffold).
            # Treat its filesystem stub plans as orphans for now.
            mod = None

        stub_fns: set[str] = set()
        if mod is not None:
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if callable(attr) and is_stub(attr):
                    stub_fns.add(attr_name)

        plan_stub_fns: set[str] = set()
        for p in child.glob("test_stub_*.txt"):
            fn = p.stem[len("test_stub_"):]
            plan_stub_fns.add(fn)

        for fn in stub_fns - plan_stub_fns:
            report.orphan_stubs.append(f"{lib_name}:{fn}")
        for fn in plan_stub_fns - stub_fns:
            report.orphan_plans.append(f"{lib_name}:{fn}")
    return report


# --- Plan execution ---------------------------------------------------


def run_plan_file(path: str | Path, learn: bool = False) -> PlanResult:
    p = Path(path)
    text = p.read_text()
    result = run_text(text, plan_path=str(p))
    if learn:
        new_text = _emit_learned(text, result)
        if new_text != text:
            diff = "".join(
                difflib.unified_diff(
                    text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=str(p),
                    tofile=str(p) + " (learned)",
                )
            )
            print(diff, end="")
            p.write_text(new_text)
    return result


def run_text(text: str, plan_path: str | None = None) -> PlanResult:
    """Parse and execute the given plan text."""
    seed_registry.pin(HARNESS_MASTER_SEED)
    saved: dict[str, Any] = {}
    result = PlanResult(path=plan_path or "<inline>")

    try:
        steps = parser.parse(text)
    except parser.ParseError as e:
        result.status = "error"
        result.steps.append(
            StepResult(
                step=Step(call="<parse>"),
                status="error",
                message=str(e),
            )
        )
        return result

    for step in steps:
        sr = _run_step(step, saved, plan_path)
        result.steps.append(sr)
        if sr.status == "stubbed":
            stub_id = sr.stub_target
            if stub_id not in result.stubbed_calls:
                result.stubbed_calls.append(stub_id)
            # Halt: later steps may depend on the stub's output.
            break
        if sr.status in ("fail", "error"):
            result.status = sr.status
            break

    if result.status == "pass" and any(
        s.status in ("fail", "error") for s in result.steps
    ):
        result.status = "fail"
    return result


def _run_step(
    step: Step, saved: dict[str, Any], plan_path: str | None
) -> StepResult:
    # Resolve any $var references in args
    try:
        kwargs = {k: _resolve(v, saved) for k, v in step.args.items()}
    except KeyError as e:
        return StepResult(
            step=step,
            status="error",
            message=f"undefined variable: {e}",
        )

    # Locate function
    if "." not in step.call:
        return StepResult(
            step=step,
            status="error",
            message=f"call lacks '.': {step.call!r}",
        )
    parts = step.call.split(".")
    module = None
    module_path = ""
    remaining: list[str] = []
    last_import_error = None
    for split_at in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:split_at])
        try:
            module = importlib.import_module(f"libs.{candidate}")
            module_path = candidate
            remaining = parts[split_at:]
            break
        except ImportError as e:
            last_import_error = e
            continue
        except Exception as e:
            return StepResult(
                step=step,
                status="error",
                message=f"import libs.{candidate}: {e}",
            )
    if module is None:
        return StepResult(
            step=step,
            status="error",
            message=f"import libs.{parts[0]}: {last_import_error}",
        )
    obj = module
    for attr in remaining:
        obj = getattr(obj, attr, None)
        if obj is None:
            return StepResult(
                step=step,
                status="error",
                message=f"libs.{module_path} has no attribute path {'.'.join(remaining)!r}",
            )
    fn = obj
    fn_name = remaining[-1]

    # Stub gate
    if is_stub(fn) and not _is_own_stub_plan(
        plan_path, module_path, fn_name
    ):
        target = f"{module_path}:{fn_name}"
        print(f"Stubbed: {target}")
        return StepResult(
            step=step, status="stubbed", stub_target=target
        )

    # Execute, capturing stdout for `expect prints =` and exceptions
    # for `expect raises =`.
    buf = io.StringIO()
    raised: BaseException | None = None
    rv: Any = None
    try:
        with redirect_stdout(buf):
            rv = fn(**kwargs)
    except BaseException as e:  # noqa: BLE001 — test harness must catch all
        raised = e

    actual: dict[str, Any] = {"return": rv, "prints": buf.getvalue()}
    if raised is not None:
        actual["raises"] = type(raised).__name__

    # `expect raises = ...` swallows the exception; otherwise it
    # bubbles to a failure.
    if raised is not None and "raises" not in step.expects:
        tb = "".join(
            traceback.format_exception(
                type(raised), raised, raised.__traceback__
            )
        )
        return StepResult(
            step=step,
            status="error",
            actual=actual,
            message=f"unexpected exception:\n{tb}",
        )

    # Match expectations
    tolerance = step.tolerance if step.tolerance is not None else DEFAULT_TOLERANCE
    for field_name, expected in step.expects.items():
        expected_resolved = _resolve(expected, saved)
        # `expect contains = "..."` — substring check against the return value.
        if field_name == "contains":
            return_val = actual.get("return")
            contained = (
                isinstance(return_val, str)
                and isinstance(expected_resolved, str)
                and expected_resolved in return_val
            )
            if not contained:
                return StepResult(
                    step=step,
                    status="fail",
                    actual=actual,
                    message=(
                        f"expect contains = {_fmt(expected_resolved)};\n"
                        f"actual return = {_fmt(return_val)}"
                    ),
                )
            continue
        got = actual.get(field_name)
        normalizable = (
            step.normalize
            and field_name in ("return", "prints")
            and isinstance(got, str)
            and isinstance(expected_resolved, str)
        )
        float_mask = step.expect_masks.get(field_name)
        zero_scale = step.expect_zero_scales.get(field_name)
        matched = (
            _equal(
                _normalize_text(got, step.normalize),
                _normalize_text(expected_resolved, step.normalize),
            )
            if normalizable
            else _close_within_tolerance(got, expected_resolved, tolerance, float_mask, zero_scale)
        )
        if not matched:
            return StepResult(
                step=step,
                status="fail",
                actual=actual,
                message=(
                    f"expect {field_name} = {_fmt(expected_resolved)};\n"
                    f"actual    = {_fmt(got)}"
                ),
            )

    if step.save_as:
        saved[step.save_as] = rv

    # If stdout was non-empty and not asserted, echo it so users
    # see prints from real (non-stub) code during test runs.
    captured = buf.getvalue()
    if captured and "prints" not in step.expects:
        sys.stdout.write(captured)

    return StepResult(step=step, status="pass", actual=actual)


def _is_own_stub_plan(
    plan_path: str | None, lib: str, fn: str
) -> bool:
    if plan_path is None:
        return False
    parts = os.path.normpath(plan_path).split(os.sep)
    if len(parts) < 2:
        return False
    return (
        parts[-2] == lib and parts[-1] == f"test_stub_{fn}.txt"
    )


def _resolve(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, VarRef):
        if value.name not in saved:
            raise KeyError(value.name)
        return saved[value.name]
    return value


def _normalize_text(text: str, rules: list[tuple[str, str]]) -> str:
    for pattern, repl in rules:
        text = re.sub(pattern, repl, text)
    return text


def _unwrap_numpy_scalar(x: Any) -> Any:
    if isinstance(x, np.generic):
        return x.item()
    return x


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        try:
            return np.array_equal(np.asarray(a), np.asarray(b))
        except Exception:
            return False
    return _unwrap_numpy_scalar(a) == _unwrap_numpy_scalar(b)


def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _within_tolerance_band(actual: Any, expected: Any, tolerance: float, zero_scale: Any = 0) -> Any:
    """Elementwise abs(actual - expected) <= tolerance * max(abs(expected), zero_scale).
    For a nonzero target, zero_scale is 0 and the band is the relative
    `tolerance * abs(expected)`. For a float-spelled zero target, zero_scale is
    the digit-derived surrogate magnitude (10 ** (1 - decimal_places)), so the
    band is `tolerance * zero_scale` instead of zero. Returns a numpy bool."""
    band = tolerance * np.maximum(np.abs(expected), zero_scale)
    return np.abs(actual - expected) <= band


def _close_within_tolerance(
    actual: Any, expected: Any, tolerance: float, float_mask: Any = None, zero_scale: Any = None
) -> bool:
    scale = 0 if zero_scale is None else zero_scale
    if isinstance(actual, np.ndarray) or isinstance(expected, np.ndarray):
        try:
            a = np.asarray(actual)
            b = np.asarray(expected)
            if float_mask is not None:
                tol_ok = _within_tolerance_band(a, b, tolerance, scale)
                exact_ok = (a == b)
                return bool(np.where(float_mask, tol_ok, exact_ok).all())
            # No spelling mask (e.g. a $var-sourced array): gate on dtype.
            if b.dtype.kind == "f":
                return bool(_within_tolerance_band(a, b, tolerance, scale).all())
            return bool(np.array_equal(a, b))
        except Exception:
            return False
    a = _unwrap_numpy_scalar(actual)
    b = _unwrap_numpy_scalar(expected)
    if isinstance(b, float) and _is_numeric(a):
        return bool(_within_tolerance_band(a, b, tolerance, scale))
    return a == b


def _fmt(v: Any) -> str:
    if isinstance(v, np.ndarray):
        if v.ndim <= 1:
            return repr(v.tolist())
        return "\n" + "\n".join(
            "  " + " ".join(str(x) for x in row) for row in v
        )
    return repr(v)


# --- Learn-mode emission ----------------------------------------------


def _emit_learned(original_text: str, result: PlanResult) -> str:
    """Rewrite the plan with actual values filled into `expect`.

    Strategy: split the original text into stanzas by blank
    lines, match each stanza in order to a StepResult, and
    rewrite that stanza in canonical form. Comments are
    preserved verbatim where they sit.
    """
    raw_stanzas = _split_stanzas(original_text)
    out_pieces: list[str] = []
    si = 0  # index into result.steps

    for kind, body in raw_stanzas:
        if kind == "blank":
            out_pieces.append(body)
            continue
        if kind == "comment":
            out_pieces.append(body)
            continue
        if si >= len(result.steps):
            # No matching step (plan halted at stub). Keep as-is.
            out_pieces.append(body)
            continue
        sr = result.steps[si]
        si += 1
        if sr.status in ("stubbed", "error"):
            out_pieces.append(body)
            continue
        out_pieces.append(_rewrite_stanza(body, sr))

    return "".join(out_pieces)


def _split_stanzas(text: str) -> list[tuple[str, str]]:
    """Split text into ('stanza'|'blank'|'comment', body_with_newline).

    A stanza is a maximal run of non-blank lines starting with
    'call:'. Standalone comment runs (between stanzas) are kept
    as 'comment' so they survive re-emission.
    """
    lines = text.splitlines(keepends=True)
    pieces: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            pieces.append(("blank", line))
            i += 1
            continue
        # Build a non-blank run
        start = i
        while i < len(lines) and lines[i].strip() != "":
            i += 1
        body = "".join(lines[start:i])
        # Determine if it contains a `call:` line
        if any(
            ln.strip().startswith("call:") for ln in lines[start:i]
        ):
            pieces.append(("stanza", body))
        else:
            pieces.append(("comment", body))
    return pieces


def _rewrite_stanza(body: str, sr: StepResult) -> str:
    """Replace `expect return` and `expect prints` in `body` with
    actuals, preserving everything else line-for-line."""
    new_expects: dict[str, Any] = {}
    if "return" in sr.step.expects:
        value = sr.actual.get("return")
        new_expects["return"] = (
            _normalize_text(value, sr.step.normalize)
            if sr.step.normalize and isinstance(value, str)
            else value
        )
    if "prints" in sr.step.expects:
        value = sr.actual.get("prints", "")
        new_expects["prints"] = (
            _normalize_text(value, sr.step.normalize)
            if sr.step.normalize and isinstance(value, str)
            else value
        )
    if "raises" in sr.step.expects:
        new_expects["raises"] = sr.actual.get("raises", "")

    if not new_expects:
        return body

    lines = body.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        match = None
        for f in new_expects:
            if stripped.startswith(f"expect {f}"):
                match = f
                break
        if match is None:
            out.append(line)
            i += 1
            continue
        # Skip the existing expect block (line plus any indented
        # continuation), then emit a fresh canonical version.
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt == "" or not nxt[:1].isspace():
                break
            if not nxt.strip().startswith("#") and (
                nxt.startswith(" ") or nxt.startswith("\t")
            ):
                i += 1
                continue
            break
        out.append(_format_expect(match, new_expects[match]))
    return "".join(out)


def _format_expect(name: str, value: Any) -> str:
    if isinstance(value, np.ndarray):
        rows = "\n".join(
            "  " + " ".join(str(x) for x in row) for row in value
        )
        return f"expect {name} =\n{rows}\n"
    return f"expect {name} = {_format_scalar(value)}\n"


def _escape_control_bytes(s: str) -> str:
    """Rewrite single-byte non-printable characters as `\\xHH`.

    Matches the parser's `\\xHH` grammar so a learned string round-trips.
    Characters at or above U+0100 are left untouched: a three-or-more-digit
    `\\x` spelling would not parse back as the same character.
    """
    return "".join(
        f"\\x{ord(c):02x}" if not c.isprintable() and ord(c) < 0x100 else c
        for c in s
    )


def _format_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        escaped = (
            v.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        escaped = _escape_control_bytes(escaped)
        return f'"{escaped}"'
    if isinstance(v, (np.integer,)):
        return str(int(v))
    if isinstance(v, (np.floating,)):
        return repr(float(v))
    return repr(v)


# --- Plan-introspection helpers (used by the self-test) ---------------


def count_stanzas(text: str) -> int:
    """How many call-stanzas does `text` contain?"""
    return len(parser.parse(text))


def result_status(result: PlanResult) -> str:
    return result.status


def result_stubbed_calls(result: PlanResult) -> list[str]:
    return list(result.stubbed_calls)


def result_step_count(result: PlanResult) -> int:
    return len(result.steps)


def result_first_message(result: PlanResult) -> str:
    for s in result.steps:
        if s.message:
            return s.message
    return ""


def coverage_ok(report: CoverageReport) -> bool:
    return report.ok


def learned_text(text: str) -> str:
    """Run `text` as a plan and return the learn-mode rewritten text."""
    result = run_text(text)
    return _emit_learned(text, result)
