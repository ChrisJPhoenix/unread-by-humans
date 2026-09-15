"""dev_test — shared library-tester orchestration for music, create, and animation."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from libs import test_harness
from libs import live_gate


# ---------------------------------------------------------------------------
# Pure, harness-testable helpers
# ---------------------------------------------------------------------------


def summary_line(n_pass: int, n_fail: int, n_err: int, n_stub: int) -> str:
    """Return a single-line human-readable test summary string."""
    return (
        f"summary: {n_pass} passed, {n_fail} failed, "
        f"{n_err} error(s); {n_stub} unique stub(s) hit"
    )


def collect_unique(existing: list, new_items: list) -> list:
    """Return a new list = existing plus any items from new_items not already present.

    Neither argument is mutated; order is preserved.
    """
    result = list(existing)
    for item in new_items:
        if item not in result:
            result.append(item)
    return result


def collect_unique_json(existing_json: str, new_json: str) -> str:
    """JSON-string wrapper for collect_unique."""
    existing = json.loads(existing_json)
    new_items = json.loads(new_json)
    return json.dumps(collect_unique(existing, new_items))


def tally(statuses: list) -> list:
    """Return [n_pass, n_fail, n_err] counted from a list of status strings."""
    n_pass = statuses.count("pass")
    n_fail = statuses.count("fail")
    n_err = len(statuses) - n_pass - n_fail
    return [n_pass, n_fail, n_err]


def tally_json(statuses_json: str) -> str:
    """JSON-string wrapper for tally."""
    return json.dumps(tally(json.loads(statuses_json)))


def aggregate_exit_code(domain1_ok: bool, domain2_ok: bool, domain3_ok: bool) -> int:
    """Return 0 if all three domains passed, else 1."""
    return 0 if (domain1_ok and domain2_ok and domain3_ok) else 1


def status_label(ok: bool) -> str:
    """Return 'PASS' if ok, else 'FAIL'."""
    return "PASS" if ok else "FAIL"


def failure_log_path(prog: str, tmp_dir_str: str) -> str:
    """Return the path string where the test failure log is written."""
    return str(Path(tmp_dir_str) / f"{prog}_test_last.txt")


def all_green_line(prog: str) -> str:
    """Return the brief success line printed when all tests pass."""
    return f"{prog} test: all green"


def live_test_id(path_str: str) -> str:
    """Return a live-test module's stable id: '<lib-dir>/<module-stem>'."""
    p = Path(path_str)
    return f"{p.parent.name}/{p.stem}"


def minutes_since(now: float, last_green: float) -> int:
    """Whole minutes elapsed from last_green to now (never negative)."""
    return max(0, int((now - last_green) // 60))


def live_report_line(action: str, reason: str, test_id: str, minutes_ago: int) -> str:
    """The audible one-line report for a gated live test's gate decision.

    Auditable like the `Stubbed:` line so "when did the SDK last really pass"
    is always visible — see design/testing.md "Gated live tests". A skip for
    any reason other than "fresh" (e.g. a keyless caller) must not borrow the
    "fresh, Nm ago" wording, since that would claim a real pass happened
    moments ago when the test never ran at all.
    """
    if action == "skip":
        if reason == "fresh":
            return f"Gated (fresh, {minutes_ago}m ago): {test_id}"
        return f"Gated (skipped, {reason}): {test_id}"
    if action == "run":
        if reason == "forced":
            return f"Gated (forced): running {test_id}"
        return f"Gated (stale/changed): running {test_id}"
    if action == "fail":
        return f"Gated FAIL ({reason}): {test_id}"
    return f"Gated ?? ({action}): {test_id}"


_running_live_test = ""


def set_running_live_test(test_id: str) -> None:
    """Record which gated live test is executing (or "" for none).

    The SIGALRM watchdog fires from a signal handler that receives no
    arguments, so the only way its message can name the phase the run was in
    is to read it back off module state that the tier keeps current.
    """
    global _running_live_test
    _running_live_test = test_id


def running_live_test() -> str:
    """The gated live test currently executing, or "" when the run is in the local domains."""
    return _running_live_test


def running_live_test_probe_json(test_id: str) -> str:
    """Exercise the set/read/clear round-trip the watchdog message depends on."""
    before = running_live_test()
    set_running_live_test(test_id)
    during = running_live_test()
    set_running_live_test("")
    return json.dumps(
        {"before": before, "during": during, "after": running_live_test()},
        sort_keys=True,
        separators=(",", ":"),
    )


def failure_notice_line(log_path_str: str) -> str:
    """Return the failure notice line printed when tests fail, including the log file path."""
    return f"FAILED — see {log_path_str}"


def filter_matched_no_plans(lib_filter, n_plans) -> bool:
    """True iff a filter was supplied and it matched no plans.

    Fires only when `lib_filter` is a non-empty list and `n_plans == 0`.
    `lib_filter` being `None` or empty means an UNFILTERED run, and that
    finding zero plans is a broken checkout, not a usage error — so this
    stays False for that case, on purpose. Total: a non-list `lib_filter` or
    a non-int `n_plans` also returns False rather than raising.
    """
    if not isinstance(lib_filter, list) or not lib_filter:
        return False
    if not isinstance(n_plans, int):
        return False
    return n_plans == 0


def filter_matched_no_plans_json(lib_filter_json: str, n_plans) -> bool:
    """JSON-string wrapper for filter_matched_no_plans's list-shaped `lib_filter` arg.

    The plan format's `arg` values are scalars/`$var` refs with no
    list-of-strings literal (see `ceiling_mode_from_argv_json`), so
    `lib_filter` travels as JSON text here; the bool return needs no such
    wrapping since `expect return = true`/`false` already works directly.
    """
    return filter_matched_no_plans(json.loads(lib_filter_json), n_plans)


def unmatched_filter_message(lib_filter, libs_dir, mode) -> str:
    """The stderr text for a filter that was supplied but matched no plans.

    Shared by the SOLO and AGGREGATE dispatch branches so the wording never
    drifts between them. Names every supplied filter value, the libs
    directory searched, and the mode, then appends a hint that a filter is a
    library DIRECTORY NAME, not a path — with a bare-name suggestion for
    every supplied value that starts with `libs/` (e.g. `libs/foo` ->
    `foo`), or no suggestion at all when none did. Pure string assembly: it
    never checks whether a suggested name actually exists.

    Total, matching this module's convention: a non-list `lib_filter`
    contributes no suggestions (rather than raising on the `for` loop below),
    and a non-string entry inside the list is skipped (rather than raising on
    `.startswith`).
    """
    base = f"no test plans matched libs={lib_filter} under {libs_dir} (mode={mode})"
    hint = "a filter names a library directory, not a path"
    values = lib_filter if isinstance(lib_filter, list) else []
    suggestions = [
        value[len("libs/"):] for value in values
        if isinstance(value, str) and value.startswith("libs/")
    ]
    if suggestions:
        hint += "; try " + ", ".join(repr(s) for s in suggestions) + " instead"
    return f"{base} — {hint}"


def unmatched_filter_message_json(lib_filter_json: str, libs_dir: str, mode: str) -> str:
    """JSON-string wrapper for unmatched_filter_message's list-shaped `lib_filter` arg."""
    return unmatched_filter_message(json.loads(lib_filter_json), libs_dir, mode)


def no_plans_found_message(libs_dir, mode) -> str:
    """The stderr text for an UNFILTERED run that found no plans at all.

    A different complaint from `unmatched_filter_message`'s "your filter
    matched nothing": this path is only reached when no filter was supplied
    (see `_dispatch_domains`'s SOLO branch), so it takes no `lib_filter`
    argument — printing `libs=None` on this path would be noise, not signal.
    """
    return f"no test plans found under {libs_dir} (mode={mode})"


def silence_logging_probe() -> str:
    """Probe `_silence_logging` against a known baseline root-logger state.

    The probe runs nested inside `run_test_cli`'s own outer `_silence_logging`,
    so it cannot read the ambient root state as its "logging works" baseline —
    it would measure the outer wrap instead. It therefore installs a plain
    WARNING-level root with no handlers, emits around the context, and puts the
    ambient state back. Emitting *after* the context exits is what makes
    restoration an observable behaviour rather than a field comparison.

    Returns "before=<n> inside=<n> after=<n> restored=<bool>", the counts being
    records that reached a capturing handler.
    """
    root = logging.getLogger()
    ambient = (root.handlers[:], root.level)
    logger = logging.getLogger("dev_test_silence_probe")
    logger.propagate = False  # keep the probe's own records out of the run's output
    records: list = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    try:
        root.handlers = []
        root.setLevel(logging.WARNING)
        logger.warning("before")
        before = len(records)
        with _silence_logging():
            logger.warning("inside")
        inside = len(records) - before
        logger.warning("after")
        after = len(records) - before - inside
        restored = (root.handlers == [] and root.level == logging.WARNING)
    finally:
        logger.removeHandler(handler)
        root.handlers, root.level = ambient[0], ambient[1]
    return f"before={before} inside={inside} after={after} restored={restored}"


def progress_bar(done: int, total: int, label: str, width: int = 20) -> str:
    """Return a single-line progress bar string (pure, no I/O).

    Format: '<bar>| <done>/<total> <label>'
    The bar is `width` characters of '#' (filled) and '-' (remaining).
    When total <= 0, filled is 0 and done is shown as-is (no ZeroDivisionError).
    """
    if total > 0:
        done = max(0, min(done, total))
        filled = round(done / total * width)
        filled = max(0, min(filled, width))
    else:
        filled = 0
    bar = "#" * filled + "-" * (width - filled)
    return f"{bar}| {done}/{total} {label}"


# ---------------------------------------------------------------------------
# Watchdog timing — committed per-mode hang-detection ceilings that only
# ratchet up
# ---------------------------------------------------------------------------

_TIMEOUT_FILE_NAME = "timeout_seconds.txt"

CEILING_MODES = ("quick", "full")

# Exit codes 0/1/3/4/5 are already taken — see design/testing.md § Exit codes.
TEST_TIMED_OUT_EXIT_CODE = 5
TIMEOUT_FILE_UNUSABLE_EXIT_CODE = 6


class TimeoutFileUnusable(Exception):
    """The hang-detection ceiling file is missing, malformed, or incomplete.

    The ceiling is the only thing standing between an infinite hang and a
    wedged session, so there is no safe value to fall back to: a silent
    default would hide the fact that the ceiling in force is no longer the
    value anyone actually chose. A run that cannot read a trustworthy
    ceiling must fail loudly instead of guessing one.
    """


def timeout_file_path(libs_dir: Path) -> Path:
    """Return the path to the committed file holding the test run's hang-detection ceilings."""
    return Path(libs_dir) / "dev_test" / _TIMEOUT_FILE_NAME


def parse_timeout_file(text: str) -> dict:
    """Return {mode: whole seconds} from the ceiling file's text.

    Strict on purpose — see TimeoutFileUnusable. Raises rather than repairing.
    """
    ceilings: dict = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2:
            raise TimeoutFileUnusable(f"malformed ceiling line: {line!r}")
        mode, value_str = fields
        if mode not in CEILING_MODES:
            raise TimeoutFileUnusable(f"unknown ceiling mode: {mode!r}")
        if mode in ceilings:
            raise TimeoutFileUnusable(f"repeated ceiling mode: {mode!r}")
        try:
            value = int(value_str)
        except ValueError:
            raise TimeoutFileUnusable(
                f"non-integer ceiling value for {mode!r}: {value_str!r}"
            ) from None
        if value <= 0:
            raise TimeoutFileUnusable(f"ceiling for {mode!r} must be > 0, got {value}")
        ceilings[mode] = value
    if set(ceilings) != set(CEILING_MODES):
        raise TimeoutFileUnusable(
            f"ceiling file must define exactly {sorted(CEILING_MODES)}, got {sorted(ceilings)}"
        )
    return ceilings


def parse_timeout_file_json(text: str) -> str:
    """JSON-string wrapper for parse_timeout_file (harness probe)."""
    return json.dumps(parse_timeout_file(text), sort_keys=True)


def render_timeout_file(ceilings: dict) -> str:
    """Render {mode: seconds} back to the file's two-line text, in CEILING_MODES order.

    The only place the format is written, so it and parse_timeout_file stay
    each other's inverse.
    """
    return "".join(f"{mode} {ceilings[mode]}\n" for mode in CEILING_MODES)


def ceiling_mode_from_argv(argv: list) -> str:
    """Which stored ceiling governs a run with these CLI args.

    `--learn` takes the full ceiling: it is the roomier of the two, and a
    ceiling that is too generous merely delays catching a hang whereas one
    that is too tight kills a healthy run outright.
    """
    return "full" if ("--full" in argv or "--learn" in argv) else "quick"


def ceiling_mode_from_argv_json(argv_json: str) -> str:
    """JSON-string wrapper for ceiling_mode_from_argv (harness probe)."""
    return ceiling_mode_from_argv(json.loads(argv_json))


def next_timeout_seconds(elapsed_seconds: float, current_timeout_seconds: int) -> int:
    """Return the timeout to store after a run that took elapsed_seconds.

    A run consuming more than two thirds of its ceiling is close enough to
    tripping the watchdog that the ceiling is doubled off the observed
    duration; anything roomier leaves the stored value alone. The value only
    ever rises: the file's job is to catch an infinite hang, not to hold the
    suite to a performance budget, so a suite that grows is accommodated
    while a suite that shrinks costs nothing.
    """
    if elapsed_seconds > current_timeout_seconds * 2.0 / 3.0:
        return max(current_timeout_seconds, math.ceil(elapsed_seconds * 2))
    return current_timeout_seconds


def read_timeout_ceilings(libs_dir: Path) -> dict:
    """Read the stored per-mode ceilings; a missing or unreadable file raises TimeoutFileUnusable."""
    path = timeout_file_path(libs_dir)
    try:
        text = path.read_text()
    except OSError as unreadable:
        raise TimeoutFileUnusable(f"cannot read {path}: {unreadable}") from unreadable
    return parse_timeout_file(text)


def write_timeout_ceilings(libs_dir: Path, ceilings: dict) -> None:
    """Store all per-mode ceilings, replacing the file's contents.

    Writes every mode every time, so ratcheting one mode's ceiling never
    drops the other mode's stored value.
    """
    timeout_file_path(libs_dir).write_text(render_timeout_file(ceilings))


def _raise_timeout_ceiling_if_run_was_slow(
    libs_dir: Path, elapsed_seconds: float, mode: str, ceilings: dict
) -> None:
    """Store a roomier ceiling when this run came close to tripping the watchdog, and say so."""
    current_timeout_seconds = ceilings[mode]
    new_timeout_seconds = next_timeout_seconds(elapsed_seconds, current_timeout_seconds)
    if new_timeout_seconds != current_timeout_seconds:
        updated = dict(ceilings)
        updated[mode] = new_timeout_seconds
        write_timeout_ceilings(libs_dir, updated)
        print(timeout_raised_line(elapsed_seconds, new_timeout_seconds, mode))


def _format_seconds(value: float) -> str:
    """Round a duration in seconds to the nearest whole second for display."""
    return str(int(round(value)))


def timeout_raised_line(elapsed_seconds: float, new_timeout_seconds: int, mode: str) -> str:
    """Announce that a slow run pushed the stored per-mode hang-detection ceiling up."""
    return (
        f"dev test: {mode} run took {_format_seconds(elapsed_seconds)}s; raised the {mode} "
        f"ceiling in libs/dev_test/timeout_seconds.txt to {new_timeout_seconds}s"
    )


def watchdog_timeout_message(
    elapsed_seconds: float, limit_seconds: float, mode: str, running_live_test: str = ""
) -> str:
    """Format the watchdog's failure message, naming WHERE the run hung.

    The two phases call for opposite responses, so the message must not treat
    them alike. A trip in the local domains is a genuine wedge worth hunting.
    A trip inside a gated live test is a real Claude session over the network
    overrunning a ceiling that was never sized for one — expected occasionally,
    and not a code defect — so that message says "re-run" instead of sending
    the reader hunting for a hang that does not exist.
    """
    opening = (
        f"dev test: no result after {_format_seconds(elapsed_seconds)}s, over the "
        f"{_format_seconds(limit_seconds)}s {mode} ceiling in libs/dev_test/timeout_seconds.txt "
        f"— treating this as a hang. "
    )
    if running_live_test:
        return opening + (
            f"It timed out in the LIVE-API test {running_live_test}, which drives a real "
            f"Claude session over the network — an occasional overrun there is expected and "
            f"is not a code hang. Re-run it, or raise that file's {mode} value by hand; a "
            f"timed-out run never raises it itself."
        )
    return opening + (
        f"It timed out in the LOCAL test domains, with no live-API test running, so this is "
        f"a real hang: find the wedged test rather than raising the ceiling. A timed-out run "
        f"never raises the ceiling itself; raise that file's {mode} value by hand only if the "
        f"local suite is legitimately slower."
    )


def run_duration_line(elapsed_seconds: float, limit_seconds: float, mode: str) -> str:
    """Report how long a run took against the ceiling it was held to."""
    return (
        f"dev test: {mode} run took {_format_seconds(elapsed_seconds)}s "
        f"({mode} ceiling {_format_seconds(limit_seconds)}s)"
    )


class _ProgressReporter:
    """Write a live single-line progress bar directly to /dev/tty.

    Gated by the WORK_PROGRESS environment variable; create() returns None
    when the variable is unset or /dev/tty cannot be opened.  All terminal
    I/O is isolated here so the rest of _dispatch_domains stays side-effect-free.
    """

    def __init__(self, total: int, tty) -> None:
        self.total = total
        self.tty = tty
        self._dirty = False
        self._closed = False

    @classmethod
    def create(cls, total: int):
        """Return a _ProgressReporter if WORK_PROGRESS is set and /dev/tty is available."""
        if not os.environ.get("WORK_PROGRESS"):
            return None
        try:
            tty = open("/dev/tty", "w")
        except OSError:
            return None
        return cls(total, tty)

    def show(self, done: int, label: str) -> None:
        """Overwrite the current tty line with the current progress bar."""
        self.tty.write("\r" + progress_bar(done, self.total, label) + "\033[K")
        self.tty.flush()
        self._dirty = True

    def finish(self) -> None:
        """Emit a trailing newline if anything was written, then close the tty handle."""
        try:
            if self._dirty:
                self.tty.write("\n")
                self.tty.flush()
        finally:
            if not self._closed:
                self._closed = True
                try:
                    self.tty.close()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Internal orchestration helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _silence_logging():
    """Discard all `logging` records for the duration of a test run.

    A `logger.warning(...)` from a library is neither an assertion nor
    something a human reads; in the failure log it only buries the real
    failure. Installing a NullHandler on the root logger stops
    `logging.lastResort` from writing to the captured stderr, and raising the
    root level above CRITICAL drops records at the source (the library
    loggers set no level of their own, so they inherit root's). Both are
    restored on the way out however the run exits.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = [logging.NullHandler()]
    root.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


class _WatchdogTimedOut(BaseException):
    """Raised by the SIGALRM handler when a test run exceeds its ceiling.

    Derives from BaseException, not Exception: run_test_cli wraps
    _dispatch_domains in `except Exception:` to turn a crashed domain into
    exit code 1, and the watchdog needs to abort the whole run rather than be
    quietly absorbed into an ordinary failure — the same reason
    KeyboardInterrupt and SystemExit are not Exceptions either.
    """


def _is_main_thread() -> bool:
    """True iff called from the interpreter's main thread.

    `signal.signal(SIGALRM, ...)` raises ValueError off the main thread;
    `_arm_watchdog` uses this to skip the watchdog rather than crash the run
    when `run_test_cli` is imported and called from a worker thread.
    """
    return threading.current_thread() is threading.main_thread()


def _arm_watchdog(limit_seconds: float, run_start: float, mode: str) -> bool:
    """Arm a SIGALRM watchdog that raises _WatchdogTimedOut after limit_seconds.

    Returns whether it was actually armed — False (an explicit, named skip)
    when called off the main thread, where signal.signal is unusable.
    `signal.alarm` interrupts a blocked syscall, which is what lets this catch
    a hang rather than merely a slow suite.
    """
    if not _is_main_thread():
        return False

    def _on_alarm(signum, frame):
        elapsed = time.monotonic() - run_start
        raise _WatchdogTimedOut(
            watchdog_timeout_message(elapsed, limit_seconds, mode, running_live_test())
        )

    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(max(1, int(limit_seconds)))
    return True


def _disarm_watchdog(armed: bool) -> None:
    """Cancel a previously armed SIGALRM watchdog; no-op if it was never armed."""
    if armed:
        signal.alarm(0)


def _print_step_failures(result: test_harness.PlanResult) -> None:
    for sr in result.steps:
        if sr.message and sr.status != "pass":
            for line in sr.message.splitlines():
                print(f"    {line}")


def _print_stub_list(stubbed: list) -> None:
    for s in stubbed:
        print(f"  stub: {s}")


def _run_domain1_plans(
    libs_dir: Path,
    mode: str,
    lib_filter: list | None,
    reporter=None,
) -> tuple[bool, bool]:
    """Discover and run Python plan files; return (ran, ok).

    If no plans are found, returns (False, False) and prints nothing — the
    caller is responsible for the no-plans message and the exit-code decision.
    """
    plans = test_harness.discover_plans(libs_dir, lib_filter=lib_filter, mode=mode)
    if not plans:
        return (False, False)

    statuses: list[str] = []
    stubbed: list[str] = []
    for i, plan in enumerate(plans):
        if reporter:
            reporter.show(i, plan.parent.name)
        result = test_harness.run_plan_file(plan)
        print(f"{result.status:8} {result.path}")
        _print_step_failures(result)
        statuses.append(result.status)
        stubbed = collect_unique(stubbed, result.stubbed_calls)

    n_pass, n_fail, n_err = tally(statuses)
    print()
    print(summary_line(n_pass, n_fail, n_err, len(stubbed)))
    if stubbed:
        _print_stub_list(stubbed)

    return (True, n_fail == 0 and n_err == 0)


def _run_domain1_learn(libs_dir: Path, lib_filter: list) -> int:
    """Run learn mode for Domain 1 only. Returns exit code."""
    plans = test_harness.discover_plans(libs_dir, lib_filter=lib_filter, mode="full")
    if not plans:
        print(
            f"no test plans found under {libs_dir} for {lib_filter}",
            file=sys.stderr,
        )
        return 3
    any_failed = False
    for plan in plans:
        result = test_harness.run_plan_file(plan, learn=True)
        print(f"{result.status:8} {plan}")
        if result.status in ("fail", "error"):
            any_failed = True
    return 1 if any_failed else 0


def _run_domain2_smoke(web_dir: Path) -> bool:
    """Run the web smoke test. Returns True if passed."""
    print("=== Domain 2: web smoke test ===")
    spec = importlib.util.spec_from_file_location(
        "dev_test_web_smoke_harness", str(web_dir / "smoke" / "_harness.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rc = module.main()
    return rc == 0


def _launch_domain3_js(web_dir: Path, repo_root: Path) -> dict:
    """Start `node --test` running in the background and return a handle.

    Launching here (rather than at collect time) lets the JS tests — a separate
    Node process tree that parallelizes across files — overlap the single-threaded
    Python domains instead of running strictly after them. Output is redirected to
    a temp file so a full pipe never blocks Node while the Python work proceeds.
    Prints nothing; _collect_domain3_js prints the header and results so the
    aggregate log keeps domain order. The returned handle's "status" is "running"
    (with "proc"/"out_file"/"out_path"), "no-node", or "no-files".
    """
    js_test_files = sorted(web_dir.glob("**/*.test.mjs"))
    node_path = shutil.which("node")
    if node_path is None:
        return {"status": "no-node"}
    if not js_test_files:
        return {"status": "no-files"}
    tmp_dir = repo_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_file = tempfile.NamedTemporaryFile(
        mode="w", dir=str(tmp_dir), prefix="js_test_", suffix=".txt", delete=False
    )
    proc = subprocess.Popen(
        # Cap Node at 3 of this 4-core laptop's cores so the single-threaded
        # Python domains (plans + web smoke), which run concurrently, keep one.
        [node_path, "--test", "--test-concurrency=3", *[str(p) for p in js_test_files]],
        cwd=str(repo_root),
        stdout=out_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return {
        "status": "running",
        "proc": proc,
        "out_file": out_file,
        "out_path": out_file.name,
    }


def _collect_domain3_js(handle: dict) -> bool:
    """Print the Domain-3 header and the joined JS results; return True if all passed."""
    print("=== Domain 3: JS tests (node:test) ===")
    status = handle["status"]
    if status == "no-node":
        print(
            "node not found on PATH — install Node >= 18 to run JS tests",
            file=sys.stderr,
        )
        return False
    if status == "no-files":
        print("no *.test.mjs files found under web/ — JS is untested")
        return False
    proc = handle["proc"]
    proc.wait()
    handle["out_file"].close()
    output = Path(handle["out_path"]).read_text()
    Path(handle["out_path"]).unlink(missing_ok=True)
    print(output, end="")
    return proc.returncode == 0


def _live_creds_present() -> bool:
    """True iff Claude creds are available (an env token or the OAuth creds file)."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    try:
        return os.path.exists(os.path.expanduser("~/.claude/.credentials.json"))
    except OSError:
        return False


def _import_live_test(path: Path):
    """Import a live_test_*.py module by file path (its heavy imports stay inside run())."""
    spec = importlib.util.spec_from_file_location(
        f"live_test_{path.parent.name}_{path.stem}", str(path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_live_tier(
    libs_dir: Path,
    repo_root: Path,
    no_live_tests: bool,
    force_live_tests: bool,
    skip_live_tests_if_keyless: bool = False,
) -> bool:
    """Discover libs/*/live_test_*.py, gate each via live_gate, run/skip/fail.

    Returns True iff no live test failed. Each module is imported and its
    covered_paths() called ALWAYS (so a broken import is caught even when the
    tier is suppressed); run() fires only when the gate says RUN. On a real
    RUN, green restamps the green-cache. `force_live_tests` makes the gate
    return RUN for every discovered live test regardless of the cache, but
    never bypasses the no-creds failure. `skip_live_tests_if_keyless` turns a
    no-creds environment into a skip-as-pass instead of a fail, unless
    `force_live_tests` is also set. See design/testing.md "Gated live tests".
    Prints directly (the caller runs this OUTSIDE the output buffer so the
    audit lines are visible on green too).
    """
    modules = sorted(libs_dir.glob("*/live_test_*.py"))
    if not modules:
        return True
    print("=== Gated live tests ===")
    creds = _live_creds_present()
    now = time.time()
    cache_path = live_gate.default_cache_path(repo_root)
    ok = True
    for path in modules:
        test_id = live_test_id(str(path))
        try:
            module = _import_live_test(path)
            covered = module.covered_paths()
        except Exception:
            traceback.print_exc()
            print(f"Gated FAILED (import/covered_paths): {test_id}")
            ok = False
            continue
        if no_live_tests:
            print(f"Gated (skipped, --no-live-tests): {test_id}")
            continue
        fingerprint = live_gate.covered_fingerprint(covered, repo_root)
        cache_entry = live_gate.read_cache(cache_path, test_id)
        decision = live_gate.gate_decision(
            cache_entry, now, fingerprint, creds,
            force=force_live_tests, skip_if_keyless=skip_live_tests_if_keyless,
        )
        action = decision["action"]
        if action == "skip":
            last_green = cache_entry["last_green_epoch"] if isinstance(cache_entry, dict) else now
            print(live_report_line("skip", decision["reason"], test_id, minutes_since(now, last_green)))
        elif action == "fail":
            print(live_report_line("fail", decision["reason"], test_id, 0))
            ok = False
        elif action == "run":
            print(live_report_line("run", decision["reason"], test_id, 0))
            set_running_live_test(test_id)
            try:
                rc = module.run()
            except Exception:
                traceback.print_exc()
                rc = 1
            finally:
                set_running_live_test("")
            if rc:
                print(f"Gated FAILED: {test_id}")
                ok = False
            else:
                live_gate.write_green(cache_path, test_id, now, fingerprint)
                print(f"Gated passed (restamped): {test_id}")
    return ok


def _print_aggregate_summary(
    prog: str,
    domain1_ok: bool,
    domain2_ok: bool,
    domain3_ok: bool,
) -> None:
    print(f"=== {prog} test summary ===")
    print(f"domain 1 (python plans): {status_label(domain1_ok)}")
    print(f"domain 2 (web smoke):    {status_label(domain2_ok)}")
    print(f"domain 3 (js node:test): {status_label(domain3_ok)}")


def _dispatch_domains(
    *,
    prog: str,
    libs_dir: Path,
    web_dir,
    repo_root: Path,
    mode: str,
    lib_filter: list | None,
) -> int:
    """Run the stub-coverage check and the requested domain(s); return an exit code.

    All output goes to the current stdout/stderr (the caller redirects these into
    a buffer). Raises if a domain raises; the caller is responsible for catching.
    """
    cov = test_harness.check_stub_coverage(libs_dir)
    if not cov.ok:
        print("Stub-coverage check FAILED:", file=sys.stderr)
        for s in cov.orphan_stubs:
            print(f"  @stub without test_stub_<fn>.txt: {s}", file=sys.stderr)
        for p in cov.orphan_plans:
            print(f"  test_stub_<fn>.txt with no matching @stub: {p}", file=sys.stderr)
        return 4

    n_plans = len(test_harness.discover_plans(libs_dir, lib_filter=lib_filter, mode=mode))
    if filter_matched_no_plans(lib_filter, n_plans):
        # Checked before the reporter starts and before any JS subprocess is
        # launched, so this usage error never needs progress-bar or
        # subprocess cleanup — see design/testing.md § Exit codes.
        print(unmatched_filter_message(lib_filter, libs_dir, mode), file=sys.stderr)
        return 3
    total = n_plans + (2 if web_dir is not None else 0)
    reporter = _ProgressReporter.create(total)

    try:
        if web_dir is None:
            # SOLO mode: Domain 1 only, no headers, no aggregate summary.
            ran, ok = _run_domain1_plans(libs_dir, mode, lib_filter, reporter=reporter)
            if not ran:
                print(no_plans_found_message(libs_dir, mode), file=sys.stderr)
                return 3
            if reporter:
                reporter.show(n_plans, "done")
            return 0 if ok else 1

        # AGGREGATE mode: all three domains. Launch the JS tests first so the
        # Node process tree overlaps the single-threaded Python domains (plans +
        # web smoke) instead of running strictly after them; join at the end.
        js_handle = _launch_domain3_js(web_dir, repo_root)
        _, domain1_ok = _run_domain1_plans(libs_dir, mode, lib_filter, reporter=reporter)
        if reporter:
            reporter.show(n_plans, "web smoke (js running)")
        domain2_ok = _run_domain2_smoke(web_dir)
        if reporter:
            reporter.show(n_plans + 1, "js tests (joining)")
        domain3_ok = _collect_domain3_js(js_handle)
        if reporter:
            reporter.show(total, "done")
        _print_aggregate_summary(prog, domain1_ok, domain2_ok, domain3_ok)
        return aggregate_exit_code(domain1_ok, domain2_ok, domain3_ok)
    finally:
        if reporter:
            reporter.finish()


# ---------------------------------------------------------------------------
# Public CLI entry point
# ---------------------------------------------------------------------------


def run_test_cli(
    argv: list,
    *,
    prog: str,
    libs_dir: Path,
    web_dir,
    repo_root: Path,
) -> int:
    """Parse argv and run the appropriate test domain(s); return an exit code.

    When web_dir is None (SOLO mode), only Domain 1 runs with no domain
    headers and no aggregate summary.  When web_dir is set (AGGREGATE mode),
    all three domains run and a summary is printed.
    """
    parser = argparse.ArgumentParser(
        prog=f"{prog} test",
        description=f"{prog} library tester.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--quick",
        action="store_const",
        dest="mode",
        const="quick",
        help="Run each library's test_primary*.txt (default).",
    )
    mode_group.add_argument(
        "--full",
        action="store_const",
        dest="mode",
        const="full",
        help="Run every test_*.txt in each library.",
    )
    mode_group.add_argument(
        "--learn",
        action="store_const",
        dest="mode",
        const="learn",
        help="Rewrite expect blocks with actual values (requires named libs).",
    )
    parser.add_argument("libs", nargs="*")
    live_tests_group = parser.add_mutually_exclusive_group()
    live_tests_group.add_argument(
        "--no-live-tests",
        action="store_true",
        dest="no_live_tests",
        help="Skip the gated-live tier's run() (modules are still imported to catch breakage).",
    )
    live_tests_group.add_argument(
        "--force-live-tests",
        action="store_true",
        dest="force_live_tests",
        help="Bypass the gated-live tier's throttle and run every live test (costs real API calls).",
    )
    parser.add_argument(
        "--skip-live-tests-if-keyless",
        action="store_true",
        dest="skip_live_tests_if_keyless",
        help=(
            "Skip the gated-live tier instead of failing it when no credentials "
            "are present (what bin/green passes; --force-live-tests still overrides)."
        ),
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 3

    mode = args.mode or "quick"
    lib_filter = args.libs or None
    no_live_tests = args.no_live_tests
    force_live_tests = args.force_live_tests
    skip_live_tests_if_keyless = args.skip_live_tests_if_keyless

    if mode == "learn" and not lib_filter:
        print(
            f"{prog} test --learn requires at least one library name "
            "(refusing to rewrite the entire tree silently)",
            file=sys.stderr,
        )
        return 3

    if mode == "learn":
        return _run_domain1_learn(libs_dir, lib_filter)

    # Logging is silenced across BOTH the buffered dispatch and the unbuffered
    # live tier below: a library logger.warning is never test output, and the
    # live tier is exactly where one would be most visible. Its own print()
    # audit lines are unaffected. See design/testing.md § "Log records are not
    # test output".
    with _silence_logging():
        buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(err_buf),
        ):
            try:
                exit_code = _dispatch_domains(
                    prog=prog,
                    libs_dir=libs_dir,
                    web_dir=web_dir,
                    repo_root=repo_root,
                    mode=mode,
                    lib_filter=lib_filter,
                )
            except Exception:
                # A domain crashed outright. Capture the traceback into the log
                # rather than letting it escape and bypass the file-and-notice
                # contract; treat it as a failure.
                traceback.print_exc()
                exit_code = 1

        # Gated-live tier — runs OUTSIDE the buffered redirect above so its audit
        # lines are visible on green too (design/testing.md). Full aggregate runs
        # only: skipped for a lib-filtered run and for SOLO (web_dir is None).
        if lib_filter is None and web_dir is not None:
            if not _run_live_tier(
                libs_dir, repo_root, no_live_tests, force_live_tests,
                skip_live_tests_if_keyless=skip_live_tests_if_keyless,
            ):
                exit_code = 1

    combined = buf.getvalue() + err_buf.getvalue()
    if exit_code == 0:
        print(all_green_line(prog))
        log_path = failure_log_path(prog, str(repo_root / "tmp"))
        Path(log_path).unlink(missing_ok=True)
    else:
        log_path = failure_log_path(prog, str(repo_root / "tmp"))
        (repo_root / "tmp").mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(combined)
        print(failure_notice_line(log_path))
    return exit_code


def repo_root() -> Path:
    """Return the repository root, resolved from this file's own location.

    This is the single source of repo-path truth.  Because it resolves from
    dev_test's stable location inside libs/ (not from any app file), moving an
    app into its own directory never breaks the test path.
    """
    return Path(__file__).resolve().parents[2]


def _report_hang(prog: str, repo_root_path: Path, message: str) -> int:
    """Surface a tripped watchdog through the same file-and-notice contract as a failing run.

    A timed-out run produces no buffered stdout/stderr to write, but
    `bin/green` prints `tmp/<prog>_test_last.txt` inline on any non-zero exit
    code — so if that file were left holding a *previous* run's contents, the
    reader would be shown a stale, misleading failure instead of the hang.
    """
    print(message, file=sys.stderr)
    log_path = failure_log_path(prog, str(repo_root_path / "tmp"))
    (repo_root_path / "tmp").mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(message + "\n")
    print(failure_notice_line(log_path))
    return TEST_TIMED_OUT_EXIT_CODE


def run(argv: list, *, prog: str, web: bool = True, web_dir=None) -> int:
    """Resolve repo paths and run the library tester under a hang-detection watchdog.

    web=True runs all three domains over the shared web/ tree; web=False runs
    Domain 1 only (no web smoke / JS); an explicit web_dir overrides web.

    `--quick` and `--full` are held to separate stored ceilings (`--full` runs
    every test_*.txt and is materially slower, so one ceiling cannot serve
    both); `ceiling_mode_from_argv` picks which one governs this run. A
    missing or corrupt ceiling file is a hard failure
    (`TIMEOUT_FILE_UNUSABLE_EXIT_CODE`) rather than a silent default — see
    `TimeoutFileUnusable`. The runner always prints how long the run took
    against its ceiling, win or lose, so the timing is visible without
    waiting for a ratchet.

    The watchdog exists to fail an infinite hang, not to police the suite's
    speed, so the ceiling is only ever raised — and never by a run that
    tripped it, which would let a genuine hang keep buying itself more time.
    """
    root = repo_root()
    wd = web_dir if web_dir is not None else (root / "web" if web else None)
    libs_dir = root / "libs"
    mode = ceiling_mode_from_argv(argv)
    try:
        ceilings = read_timeout_ceilings(libs_dir)
    except TimeoutFileUnusable as unusable:
        print(f"dev test: {unusable}", file=sys.stderr)
        return TIMEOUT_FILE_UNUSABLE_EXIT_CODE
    timeout_seconds = ceilings[mode]
    run_start = time.monotonic()
    armed = _arm_watchdog(timeout_seconds, run_start, mode)
    try:
        exit_code = run_test_cli(argv, prog=prog, libs_dir=libs_dir, web_dir=wd, repo_root=root)
    except _WatchdogTimedOut as timed_out:
        return _report_hang(prog, root, str(timed_out))
    finally:
        _disarm_watchdog(armed)
    elapsed = time.monotonic() - run_start
    print(run_duration_line(elapsed, timeout_seconds, mode))
    _raise_timeout_ceiling_if_run_was_slow(libs_dir, elapsed, mode, ceilings)
    return exit_code


def app_main(argv: list, *, prog: str, web: bool = True) -> int:
    """Thin CLI router shared by the dev tool and the app shims.

    Routes the `test` subcommand to run(). Imported lazily by callers that
    only need the test tier. Prints usage on no args or -h/--help; returns 3
    on an unknown command.
    """
    if argv and argv[0] == "test":
        return run(argv[1:], prog=prog, web=web)
    if not argv or argv[0] in ("-h", "--help"):
        print(f"usage: {prog} test [--quick|--full|--learn] [libs...]")
        return 0
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 3
