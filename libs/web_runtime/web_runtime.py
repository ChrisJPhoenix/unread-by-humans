"""Path-traversal guard for the web runtime's filesystem affordance.

This is the one piece of real logic in the runtime: every path the Flask
server receives from the browser is validated here before any I/O occurs.
See design/web-runtime.md §Scope and safety.
"""
import os


class PathTraversalError(ValueError):
    """Raised when a requested path is absolute, contains a `..` escape, or resolves outside the project root."""


def resolve_in_root(root: str, rel: str) -> str:
    """Resolve project-relative `rel` against `root`, returning a safe absolute path.

    Returns os.path.realpath(root joined with rel) only if the result stays
    inside root (root itself counts as inside). Raises PathTraversalError if
    rel is absolute or if symlink/`..` resolution escapes root. realpath is
    used so symlink targets are checked too.
    """
    if os.path.isabs(rel):
        raise PathTraversalError(f"path must be relative, got: {rel!r}")
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, rel))
    if target == base or target.startswith(base + os.sep):
        return target
    raise PathTraversalError(
        f"path {rel!r} resolves to {target!r}, which is outside the project root"
    )


def fixture_symlink_root() -> str:
    """Absolute path to the on-disk symlink test fixture root (test_fixtures/root), computed from __file__ for machine-independence."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures", "root")
