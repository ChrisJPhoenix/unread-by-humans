"""Utilities for Flask dev servers to detect and report a busy port."""

import errno
import os
import socket

SUPERVISED_ENV_VAR = "MOVIE_SERVE_SUPERVISED"
NO_SUPERVISOR_ENV_VAR = "MOVIE_SERVE_NO_SUPERVISOR"


def should_handoff_to_supervisor(is_supervised: bool, no_supervisor_optout: bool) -> bool:
    """True if a directly-launched server should re-exec into its supervisor.

    A directly-launched server hands off to its supervisor unless it is
    already running under one (SUPERVISED_ENV_VAR present in environ) or the
    caller explicitly opted out (NO_SUPERVISOR_ENV_VAR present in environ).
    Presence of the key is the signal regardless of its value.

    Call with:
        is_supervised    = SUPERVISED_ENV_VAR in os.environ
        no_supervisor_optout = NO_SUPERVISOR_ENV_VAR in os.environ
    """
    return not is_supervised and not no_supervisor_optout


def reexec_into_supervisor_unless_managed(supervisor_path, forwarded_args=()):
    """Replace this process with its supervisor script (via os.execv) when it was
    launched directly rather than under the supervisor.

    Returns normally when no handoff is needed (the caller then starts the server
    itself). Does not return when it hands off — os.execv replaces the process.
    The handoff decision is the harness-tested should_handoff_to_supervisor.
    """
    if not should_handoff_to_supervisor(
        SUPERVISED_ENV_VAR in os.environ,
        NO_SUPERVISOR_ENV_VAR in os.environ,
    ):
        return
    os.execv(supervisor_path, [supervisor_path, *forwarded_args])


def port_in_use(host: str, port: int) -> bool:
    """Return True if the given host/port is already bound by another process.

    Opens a probe socket and attempts to bind it. Returns True iff the bind
    fails with EADDRINUSE. Re-raises any other OSError. Always closes the
    probe socket before returning so the real server can bind immediately after.

    The probe sets SO_REUSEADDR to match Werkzeug's allow_reuse_address=True,
    so lingering TIME_WAIT sockets (e.g. from killed SSE connections during a
    server restart) are NOT misreported as a busy port. Only a genuine
    conflicting LISTENer yields EADDRINUSE and causes this to return True.
    SO_REUSEADDR does not allow binding over an active listener without
    SO_REUSEPORT, so the live-listener case is still detected correctly.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
        return False
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return True
        raise
    finally:
        probe.close()


def _listening_inodes_on_port(port: int) -> set[str]:
    """Return the set of socket inodes that are in LISTEN state on port."""
    hex_port = f"{port:04X}"
    inodes: set[str] = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            continue
        for line in lines[1:]:  # skip header
            cols = line.split()
            if len(cols) < 10:
                continue
            local_address = cols[1]
            state = cols[3]
            inode = cols[9]
            if state == "0A" and local_address.split(":")[1] == hex_port:
                inodes.add(inode)
    return inodes


def _pid_owning_inode(inodes: set[str]) -> "int | None":
    """Return the PID whose open file descriptors include a socket inode."""
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = entry.name
        fd_dir = f"/proc/{pid}/fd"
        try:
            fd_entries = os.scandir(fd_dir)
        except OSError:
            continue
        with fd_entries:
            for fd_entry in fd_entries:
                try:
                    target = os.readlink(fd_entry.path)
                except OSError:
                    continue
                for inode in inodes:
                    if target == f"socket:[{inode}]":
                        return int(pid)
    return None


def listener_pid(port: int) -> "int | None":
    """Return the PID of the process LISTENing on port, or None if not found.

    Reads /proc/net/tcp and /proc/net/tcp6 to find socket inodes in LISTEN
    state on the given port, then walks /proc/<pid>/fd/ to find which process
    owns one of those inodes. Returns None on Linux systems without /proc or
    when no listener is found.
    """
    inodes = _listening_inodes_on_port(port)
    if not inodes:
        return None
    return _pid_owning_inode(inodes)


def busy_message(port: int, pid: "int | None") -> str:
    """Return a human-readable advisory message about a port already in use.

    If pid is known, includes a kill command. If not, suggests ss to find it.
    """
    if pid is not None:
        return (
            f"Port {port} is already in use by PID {pid}.\n"
            f"Stop it with:  kill {pid}\n"
            f"Then start this server again."
        )
    return (
        f"Port {port} is already in use, but the PID could not be identified.\n"
        f"Find it with:  ss -ltnp | grep :{port}"
    )
