"""@stub decorator.

A function decorated with `@stub` is a placeholder. The harness
recognizes it by the `_omr_stub` attribute and applies special
rules:

- The stub's own `test_stub_<function_name>.txt` exercises it
  normally and asserts its placeholder behavior.
- Every other plan that reaches a `@stub`-decorated call prints
  `Stubbed: <library>:<function>` and halts at that step. The
  steps up to and including the line-emission count as "passed
  with stub deferral"; later steps are not run.

Stubs commit to behaviour: their `test_stub_*.txt` is the
contract the real implementation must also satisfy when it
arrives.
"""
import functools


def stub(fn):
    """Mark `fn` as a stub. Behaviour at runtime is unchanged."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    wrapper._omr_stub = True
    wrapper._omr_stub_target = fn
    return wrapper


def is_stub(fn) -> bool:
    return bool(getattr(fn, "_omr_stub", False))
