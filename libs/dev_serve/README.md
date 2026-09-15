# dev_serve

## Why

Both `web/server.py` and `objects/server.py` bind port 5050. When that port
is already occupied the raw Flask error is cryptic. This library gives each
server a way to detect a busy port before calling `app.run()`, find the PID
that holds it, and print a clear advisory message so the developer knows
exactly which process to stop.

The implementation is pure-Python: it reads `/proc/net/tcp` (and `tcp6`) to
find the listening socket inode and then walks `/proc/<pid>/fd/` to match the
inode to a PID. No subprocesses (`ss`, `lsof`) are launched.

## API

```python
from libs import dev_serve

if dev_serve.port_in_use("127.0.0.1", 5050):
    pid = dev_serve.listener_pid(5050)
    print(dev_serve.busy_message(5050, pid))
    raise SystemExit(1)
```

- `port_in_use(host, port) -> bool` — returns `True` iff a `bind()` attempt
  on the given address fails with `EADDRINUSE`. Re-raises any other `OSError`.
  Always closes the probe socket before returning. The probe sets `SO_REUSEADDR`
  to match Werkzeug's `allow_reuse_address=True`, so TIME_WAIT sockets from
  killed SSE connections are not misreported as a busy port during a server
  restart; only a genuine active LISTENer still triggers `EADDRINUSE`.

- `listener_pid(port) -> int | None` — returns the PID of the process in
  LISTEN state on `port`, or `None` when it cannot be determined (e.g. the
  process exited between the two reads, or `/proc` is unavailable).

- `busy_message(port, pid) -> str` — returns a human-readable multi-line
  string. When `pid` is known it includes a `kill <pid>` command; when `pid`
  is `None` it suggests `ss -ltnp | grep :<port>`.

- `should_handoff_to_supervisor(is_supervised, no_supervisor_optout) -> bool` —
  returns `True` when a directly-launched server should re-exec into its
  supervisor script. Returns `False` when either boolean is `True`. Callers
  supply `is_supervised = SUPERVISED_ENV_VAR in os.environ` and
  `no_supervisor_optout = NO_SUPERVISOR_ENV_VAR in os.environ`.

## Env-var constants

- `SUPERVISED_ENV_VAR = "MOVIE_SERVE_SUPERVISED"` — exported by a supervisor
  script (e.g. `bin/serve`) into its child process. Presence of
  this key in the environment tells a server it is already managed and must
  not re-exec.

- `NO_SUPERVISOR_ENV_VAR = "MOVIE_SERVE_NO_SUPERVISOR"` — opt-out escape
  hatch. Set this in the environment to suppress the self-handoff (e.g. for
  debugging or one-shot manual runs).
