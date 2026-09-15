"""Path-traversal guard for the web runtime's filesystem affordance.

Every Flask endpoint that touches the disk resolves the caller-supplied
relative path through `resolve_in_root`, which rejects absolute paths and
any `..` sequence that escapes the project root. This is the
one piece of real security logic in the web runtime; the HTTP layer stays
thin by delegating all path validation here.
"""
from libs.web_runtime.web_runtime import (
    resolve_in_root,
    PathTraversalError,
    fixture_symlink_root,
)

__all__ = ["resolve_in_root", "PathTraversalError", "fixture_symlink_root"]
