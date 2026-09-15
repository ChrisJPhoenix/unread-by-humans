"""Demo library — used only to exercise the test harness.

Contains a tiny real function (`identity`) and a `@stub`
function (`future_thing`) plus its `test_stub_future_thing.txt`.
This whole library can be deleted once a real pipeline library
plays the same role; for Phase 0 it is what gives the harness
self-test something to invoke.
"""
from libs.demo.demo import identity, add, future_thing, always_raises, labeled_count, array_just_above_two_three

__all__ = ["identity", "add", "future_thing", "always_raises", "labeled_count", "array_just_above_two_three"]
