"""Dev-server port utilities: detect a busy port and report which PID holds it.

Consumers call port_in_use() before Flask's app.run(); if it returns True they
call listener_pid() and pass the result to busy_message() for a human-readable
error before exiting.
"""
from libs.dev_serve.dev_serve import (
    port_in_use,
    listener_pid,
    busy_message,
    should_handoff_to_supervisor,
    reexec_into_supervisor_unless_managed,
    SUPERVISED_ENV_VAR,
    NO_SUPERVISOR_ENV_VAR,
)

__all__ = [
    "port_in_use",
    "listener_pid",
    "busy_message",
    "should_handoff_to_supervisor",
    "reexec_into_supervisor_unless_managed",
    "SUPERVISED_ENV_VAR",
    "NO_SUPERVISOR_ENV_VAR",
]
