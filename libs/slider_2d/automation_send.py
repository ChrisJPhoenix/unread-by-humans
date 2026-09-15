"""Pure outgoing-value/dedupe helpers for real-time MIDI automation sends.

All functions are side-effect-free and depend only on the standard library,
making them straightforwardly unit-testable without a FluidSynth instance.
"""


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def outgoing_value(target: str, value: float, bend_range: float = 0.0) -> int:
    """Compute the integer MIDI value to send for one automation sample.

    Args:
        target:     "cc" for a Control Change message, "bend" for pitch-bend.
        value:      The raw float sample from the automation curve.
        bend_range: Semitone range for pitch-bend (ignored for "cc").

    Returns:
        An integer ready to pass to fluidsynth: 0–127 for CC, -8192–8191 for bend.
    """
    if target == "cc":
        return _clamp(round(value), 0, 127)
    # target == "bend"
    return _clamp(round(value / bend_range * 8192), -8192, 8191)


def automation_phase(elapsed: float, repeat_s: float) -> float:
    """Return the normalised phase [0, 1) within a repeating automation cycle.

    Args:
        elapsed:  Seconds since the automation epoch.
        repeat_s: Cycle length in seconds.  Zero disables repetition.

    Returns:
        Phase in [0, 1) when repeat_s > 0, else 0.0.
    """
    if repeat_s > 0:
        return (elapsed % repeat_s) / repeat_s
    return 0.0


def send_key(channel: int, spec: dict) -> tuple:
    """Return a hashable key identifying the MIDI destination for an automation spec.

    For CC specs the key includes the CC number so two specs on different CCs
    are tracked independently.

    Args:
        channel: MIDI channel 0–15.
        spec:    Automation descriptor dict with at least a "target" key.

    Returns:
        ``(channel, "cc", cc_number)`` for CC targets or
        ``(channel, "bend")`` for pitch-bend targets.
    """
    if spec["target"] == "cc":
        return (channel, "cc", int(spec["cc"]))
    return (channel, "bend")


def should_send(last_value: "int | None", new_value: int) -> bool:
    """Decide whether a new MIDI value should actually be transmitted.

    Suppresses redundant sends when the computed value has not changed since
    the last transmission, preventing FluidSynth ring-buffer flooding.

    Args:
        last_value: The integer value sent last time, or None if never sent.
        new_value:  The freshly computed integer value.

    Returns:
        True when the value differs from the last sent value (or was never sent).
    """
    return last_value != new_value
