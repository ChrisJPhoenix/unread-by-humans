# web_runtime

## Why

The path-traversal guard is the one piece of real logic in the web filesystem
affordance. Every relative path the browser sends to the Flask server must be
validated before any I/O occurs. See design/web-runtime.md §Scope and safety.
The consumer is `web/server.py`.

## API

```python
from libs import web_runtime

safe_path = web_runtime.resolve_in_root(root, rel)
```

- `resolve_in_root(root, rel) -> str` — returns the safe realpath of `rel`
  joined to `root`. Root itself counts as inside (empty `rel`, `"."`, or a
  trailing-slash path all resolve to root). Raises `PathTraversalError` if
  `rel` is absolute, if `..` escapes the root, or if a symlink target resolves
  outside the root.

- `PathTraversalError(ValueError)` — raised on any traversal attempt. Callers
  should map this to an HTTP 400.

- `fixture_symlink_root() -> str` — returns the path to the committed
  `test_fixtures/root` directory used by the symlink-escape test. Present so
  test plans can locate the fixture without hardcoding machine paths.
