"""Tiny placeholder lib so the harness has something to call."""
import numpy as np
from libs.test_harness.stub import stub


def identity(x):
    return x


def add(a, b):
    return a + b


def always_raises():
    raise ValueError("expected failure")


def labeled_count() -> str:
    """Return a fixed label-with-number string for normalization tests."""
    return "count: 42"


def array_just_above_two_three() -> np.ndarray:
    """Fixed 1x2 array sitting ~1e-7 above (2, 3); for per-element float-tolerance tests."""
    return np.array([[2.0000001, 3.0]])


@stub
def future_thing(n: int = 0) -> int:
    """Placeholder for whatever phase-1 work goes here.

    The stub commits to returning 0 for any input. The real
    implementation later will replace this body while keeping
    the (eventually expanded) test_stub_future_thing.txt
    contract passing.
    """
    return 0
