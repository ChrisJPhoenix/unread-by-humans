# error_report

## Why

Every app handler that catches an exception needs the same things: a canonical
dict describing what happened, a formatted copy-paste block for logs or chat,
a fingerprint for deduplication, and a storm gate that suppresses floods.
`libs/error_report/` is the boring (effect-free, deterministic) core that
provides all of these so each handler can stay thin.  `build_report` is the
live-exception entry point (Domain 2 / untested-glue layer); everything else is
harness-tested under Domain 1.  Redaction and log-write concurrency are deferred
to V2.  The broader surfacing plan this library was built against is tracked
in the source monorepo's work-plan ledger, which is not published here.

## API

```python
from libs import error_report

rpt  = error_report.report_from_parts(type_name, message, stack="", ...)
rpt  = error_report.build_report(exc, path=None, payload=None, url=None)
text = error_report.format_report(report)
key  = error_report.storm_key(report)
ok   = error_report.should_emit(key, seen_keys, distinct_count, cap=10)
env  = error_report.crash_envelope(report)
rpt  = error_report.unwrap_envelope(env)
```

- `report_from_parts(type_name, message, stack="", path=None, payload=None, url=None, time=None) -> dict` —
  assembles the canonical report dict (keys: `type`, `message`, `stack`, `time`,
  and whichever optional keys are supplied).  `stack` accepts either a
  newline-delimited string of `file:line` entries or a list of
  `{"file", "line"}` dicts.  When `time` is `None` a UTC ISO-8601 timestamp
  is substituted (supply an explicit value for deterministic output).

- `build_report(exc, *, path=None, payload=None, url=None) -> dict` —
  extracts `type`, `message`, and a structured `stack` from a live exception
  and delegates to `report_from_parts`.

- `format_report(report) -> dict` —
  renders the report as a triple-backtick code fence (message, stack, optional
  path/payload/url/time).  Handles both stack forms.

- `storm_key(report) -> str` —
  fingerprint = `type` joined with each stack frame's `file:line`; the
  `message` is excluded so varying dynamic text does not inflate key counts.

- `should_emit(key, seen_keys, distinct_count, cap=10) -> bool` —
  returns `False` if the key is already in `seen_keys` (duplicate) or
  `distinct_count >= cap` (storm cap); otherwise `True`.  `seen_keys` accepts
  set / frozenset / list, or a comma-separated string (for harness use).

- `crash_envelope(report) -> dict` —
  returns `{"__crash__": report}`.  The `__crash__` key is reserved and must
  not be written by application cards directly.

- `unwrap_envelope(envelope) -> dict` —
  extracts the inner report from a crash envelope (inverse of
  `crash_envelope`; also used by the Domain-1 test plan).
