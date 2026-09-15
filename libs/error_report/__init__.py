"""Canonical exception→report assembly, storm gate, and crash envelope.

Converts live exceptions (via `build_report`) or manually supplied parts
(via `report_from_parts`) into a normalised report dict, formats it for
human display (`format_report`), fingerprints it for deduplication
(`storm_key` / `should_emit`), and wraps it in the reserved crash envelope
(`crash_envelope`).  All functions except `build_report` (which requires a
live exception) are boring and harness-testable under Domain 1.

Deferred to V2: field redaction, log-write concurrency helpers.
"""
from libs.error_report.error_report import (
    report_from_parts,
    build_report,
    format_report,
    storm_key,
    should_emit,
    crash_envelope,
    unwrap_envelope,
)

__all__ = [
    "report_from_parts",
    "build_report",
    "format_report",
    "storm_key",
    "should_emit",
    "crash_envelope",
    "unwrap_envelope",
]
