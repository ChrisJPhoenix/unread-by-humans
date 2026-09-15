"""music_render.backend — synth backend implementations.

A backend is any object that satisfies the following interface,
called by ``render_pcm`` to produce float32 PCM samples:

    backend.note_on(note: int, velocity: int, sample_offset: int) -> None
        Activate a note (MIDI note number 0–127, velocity 0–127).
        ``sample_offset`` is the frame index within the current block
        at which the event fires.

    backend.note_off(note: int, sample_offset: int) -> None
        Deactivate a note.
        ``sample_offset`` is the frame index within the current block.

    backend.render_block(n_frames: int) -> np.ndarray
        Render exactly ``n_frames`` interleaved stereo float32 samples
        (shape ``(n_frames, 2)``).  Called after all note_on/note_off
        events for the block have been dispatched.

``FakeBackend`` is the deterministic reference implementation.
``FluidSynthBackend`` is the real offline-render backend (Phase 3);
it is untested-by-design glue (native lib + large soundfont required).
``make_fluidsynth_backend`` is its factory.

NOTE: ``fluidsynth`` (pyfluidsynth) is imported LAZILY inside
``FluidSynthBackend.__init__`` so that this module (and the whole
``music_render`` library) can be imported without fluidsynth installed.
The test harness and any caller that injects ``FakeBackend`` will never
trigger the fluidsynth import.
"""
import ctypes

import numpy as np

# ── Locked quality constants ────────────────────────────────────────────────
DEFAULT_SOUNDFONT = '/usr/share/sounds/sf2/MuseScore_General_Full.sf2'
_SAMPLE_RATE = 48000
_SYNTH_POLYPHONY = 1024
_RENDER_CHUNK = 2048

# FluidSynth log level for WARN messages (from <fluidsynth/log.h>).
_FLUID_WARN = 2


class FakeBackend:
    """Deterministic synth backend requiring no external dependencies.

    For each active note the backend contributes a constant DC level
    equal to ``note_number / 127.0`` to both stereo channels.  This
    makes output fully predictable: the caller can compute expected
    PCM from the schedule without running audio math.

    Active notes accumulate additively; the sum is clipped to [-1, 1]
    to avoid surprising overflow when many notes sound simultaneously.

    Notes are keyed by ``(channel, note)`` so the same MIDI note number
    on different channels is tracked independently.
    """

    def __init__(self) -> None:
        """Initialise with no active notes and no recorded routing state."""
        self._active: dict[tuple[int, int], float] = {}  # (channel, note) -> amplitude
        self._programs: dict[int, tuple] = {}             # channel -> (bank, program)
        self._ccs: dict[int, dict] = {}                   # channel -> {cc_number: value}
        self._bends: dict[int, int] = {}                  # channel -> bend value
        self._bend_ranges: dict[int, int] = {}            # channel -> semitones
        self._note_on_channels: set = set()               # every channel that saw a note_on

    def note_on(self, note: int, velocity: int, channel: int) -> None:
        """Activate ``note`` on ``channel`` with a deterministic amplitude derived from ``note``."""
        self._active[(channel, note)] = note / 127.0
        self._note_on_channels.add(channel)

    def note_off(self, note: int, channel: int) -> None:
        """Deactivate ``note`` on ``channel``; silently ignore if already off."""
        self._active.pop((channel, note), None)

    def cc(self, channel: int, cc_number: int, value: int) -> None:
        """Record a MIDI CC message for ``channel``."""
        self._ccs.setdefault(channel, {})[cc_number] = value

    def pitch_bend(self, channel: int, value: int) -> None:
        """Record a MIDI pitch-bend message for ``channel``."""
        self._bends[channel] = value

    def program_select(self, channel: int, bank: int, program: int) -> None:
        """Record a bank/program selection for ``channel``."""
        self._programs[channel] = (bank, program)

    def set_pitch_bend_range(self, channel: int, semitones: int) -> None:
        """Record the pitch-bend range in semitones for ``channel``."""
        self._bend_ranges[channel] = semitones

    def render_block(self, n_frames: int) -> np.ndarray:
        """Render ``n_frames`` of deterministic stereo float32 PCM.

        Returns:
            Array of shape ``(n_frames, 2)``, dtype float32.
            Each sample is the clipped sum of active-note amplitudes.
        """
        amplitude = float(np.clip(sum(self._active.values()), -1.0, 1.0))
        block = np.full((n_frames, 2), amplitude, dtype=np.float32)
        return block

    def active_notes(self) -> list:
        """Return sorted list of distinct MIDI note numbers currently active (for testing)."""
        return sorted({note for (_ch, note) in self._active})

    def active_channel_notes(self) -> list:
        """Return sorted list of (channel, note) tuples currently active."""
        return sorted(self._active)

    def note_on_channels(self) -> list:
        """Return sorted list of every channel that ever received a note_on."""
        return sorted(self._note_on_channels)

    def program_for(self, channel: int):
        """Return the (bank, program) tuple recorded for ``channel``, or None."""
        return self._programs.get(channel)

    def cc_value(self, channel: int, cc_number: int):
        """Return the last CC value recorded for ``channel`` and ``cc_number``, or None."""
        return self._ccs.get(channel, {}).get(cc_number)

    def bend_for(self, channel: int):
        """Return the last pitch-bend value recorded for ``channel``, or None."""
        return self._bends.get(channel)

    def bend_range_for(self, channel: int):
        """Return the pitch-bend range in semitones recorded for ``channel``, or None."""
        return self._bend_ranges.get(channel)


def fake_backend_render_note(note: int, n_frames: int) -> np.ndarray:
    """Render ``n_frames`` of PCM for a single active ``note`` via FakeBackend.

    Activates ``note`` at velocity 80, renders ``n_frames`` frames, then
    deactivates the note.  This is a deterministic test helper — no
    external state, always the same result for the same inputs.

    Args:
        note: MIDI note number 0–127.
        n_frames: Number of audio frames to render.

    Returns:
        NumPy array of shape ``(n_frames, 2)``, dtype float32.
    """
    backend = FakeBackend()
    backend.note_on(note, 80, 0)
    block = backend.render_block(n_frames)
    backend.note_off(note, 0)
    return block


class FluidSynthBackend:
    """Offline float32 FluidSynth backend at 48 kHz / FLUID_INTERP_HIGHEST.

    This is untested-by-design glue: it requires ``fluidsynth`` (pyfluidsynth)
    and a large soundfont file that are not available in the test sandbox.  The
    rest of the ``music_render`` pipeline is covered by ``FakeBackend`` tests;
    this class is the thin seam between those tested layers and the native lib.

    The ``fluidsynth`` import happens inside ``__init__`` so that importing
    this module without fluidsynth installed does not raise ``ImportError``.

    Quality settings are locked per design/music-web.md:
      - ``fluid_synth_write_float`` for float PCM (not int16).
      - 48000 Hz sample rate.
      - ``FLUID_INTERP_HIGHEST`` interpolation on every channel.
    """

    def __init__(self, soundfont_path: str = DEFAULT_SOUNDFONT) -> None:
        """Load the soundfont and configure the offline synth.

        Args:
            soundfont_path: Absolute path to a .sf2 soundfont file.

        Raises:
            ImportError: If ``fluidsynth`` (pyfluidsynth) is not installed.
            RuntimeError: If the soundfont cannot be loaded.
        """
        import fluidsynth  # lazy — not imported at module level

        self._fl_module = fluidsynth
        _install_quiet_fluidsynth_logging(fluidsynth)

        self._synth = fluidsynth.Synth(
            samplerate=float(_SAMPLE_RATE),
            **{'synth.polyphony': _SYNTH_POLYPHONY},
        )
        self._sfid = self._synth.sfload(soundfont_path)
        if self._sfid == -1:
            self._synth.delete()
            raise RuntimeError(f"Could not load soundfont: {soundfont_path}")

        # Apply FLUID_INTERP_HIGHEST to all channels.
        # FLUID_INTERP_HIGHEST = 7 per FluidSynth source (fluid_types.h).
        _fl = fluidsynth._fl
        _fl.fluid_synth_set_interp_method.restype = ctypes.c_int
        _fl.fluid_synth_set_interp_method.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int
        ]
        FLUID_INTERP_HIGHEST = 7
        _fl.fluid_synth_set_interp_method(self._synth.synth, -1, FLUID_INTERP_HIGHEST)

        # Without explicit argtypes, ctypes marshals every argument as a
        # 32-bit c_int, truncating the 64-bit synth pointer and segfaulting.
        _fl.fluid_synth_write_float.restype = ctypes.c_int
        _fl.fluid_synth_write_float.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ]

    def program_select(self, channel: int, bank: int, program: int) -> None:
        """Select a bank/program on a MIDI channel.

        Args:
            channel: MIDI channel 0–15.
            bank: SoundFont bank number.
            program: GM program number 0–127.
        """
        self._synth.program_select(channel, self._sfid, bank, program)

    def note_on(self, note: int, velocity: int, channel: int) -> None:
        """Activate a MIDI note on the given channel.

        Args:
            note: MIDI note number 0–127.
            velocity: Note velocity 0–127.
            channel: MIDI channel 0–15.
        """
        self._synth.noteon(channel, note, velocity)

    def note_off(self, note: int, channel: int) -> None:
        """Deactivate a MIDI note on the given channel.

        Args:
            note: MIDI note number 0–127.
            channel: MIDI channel 0–15.
        """
        self._synth.noteoff(channel, note)

    def set_pitch_bend_range(self, channel: int, semitones: int) -> None:
        """Set the pitch-bend range via RPN on ``channel``.

        Sends the standard MIDI RPN sequence: CC101=0, CC100=0, CC6=semitones,
        CC38=0 to configure the bend-range on the specified channel.

        Args:
            channel: MIDI channel 0–15.
            semitones: Pitch-bend range in semitones.
        """
        self._synth.cc(channel, 101, 0)
        self._synth.cc(channel, 100, 0)
        self._synth.cc(channel, 6, semitones)
        self._synth.cc(channel, 38, 0)

    def cc(self, channel: int, cc_number: int, value: int) -> None:
        """Send a MIDI CC message.

        For CC 76 (vibrato rate) and CC 77 (vibrato delay), also emits the
        Roland GS NRPN equivalent so SoundFonts that ignore CC 76/77 still
        receive the vibrato intent.  GS NRPN duplication for SoundFonts that
        ignore CC76/77.

        Args:
            channel: MIDI channel 0–15.
            cc_number: CC number 0–127.
            value: CC value 0–127.
        """
        self._synth.cc(channel, cc_number, value)
        if cc_number in (76, 77):
            nrpn_lsb = 0x08 if cc_number == 76 else 0x0A
            self._synth.cc(channel, 99, 0x01)        # NRPN MSB
            self._synth.cc(channel, 98, nrpn_lsb)   # NRPN LSB
            self._synth.cc(channel, 6, value)        # Data Entry MSB

    def pitch_bend(self, channel: int, value: int) -> None:
        """Send a MIDI pitch-bend message.

        Args:
            channel: MIDI channel 0–15.
            value: Pitch-bend value −8192..+8191.
        """
        self._synth.pitch_bend(channel, value)

    def render_block(self, n_frames: int) -> np.ndarray:
        """Render ``n_frames`` of float32 stereo PCM via ``fluid_synth_write_float``.

        Uses the FluidSynth C API directly to get float PCM (pyfluidsynth's
        ``get_samples`` returns int16; this backend bypasses that to stay on the
        float path as required by design/music-web.md).

        Args:
            n_frames: Number of audio frames to render.

        Returns:
            Array of shape ``(n_frames, 2)``, dtype float32.
        """
        left  = (ctypes.c_float * n_frames)()
        right = (ctypes.c_float * n_frames)()
        self._fl_module._fl.fluid_synth_write_float(
            self._synth.synth,
            n_frames,
            left,  0, 1,
            right, 0, 1,
        )
        left_array  = np.frombuffer(left,  dtype=np.float32).copy()
        right_array = np.frombuffer(right, dtype=np.float32).copy()
        return np.column_stack([left_array, right_array])

    def close(self) -> None:
        """Release the FluidSynth synth object."""
        self._synth.delete()


def _install_quiet_fluidsynth_logging(fluidsynth_module) -> None:
    """Suppress FluidSynth WARN-level stderr output for this process.

    FluidSynth prints ring-buffer overflow warnings to stderr from the render
    path; each write is slow and can flood stderr.  Passing NULL as the log
    function for FLUID_WARN silences those writes entirely.

    Args:
        fluidsynth_module: The imported ``fluidsynth`` module (passed in so
            this function has no module-level fluidsynth dependency).
    """
    _fl = fluidsynth_module._fl
    _fl.fluid_set_log_function.restype = ctypes.c_void_p
    _fl.fluid_set_log_function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    _fl.fluid_set_log_function(_FLUID_WARN, None, None)


def make_fluidsynth_backend(soundfont_path: str = DEFAULT_SOUNDFONT) -> "FluidSynthBackend":
    """Return a ``FluidSynthBackend`` loaded with the given soundfont.

    This is thin glue — untested by design (requires native fluidsynth and a
    large soundfont).  See design/music-web.md and libs/music_render/README.md.

    The ``fluidsynth`` import is lazy (inside ``FluidSynthBackend.__init__``)
    so calling this function without fluidsynth installed raises ``ImportError``
    only at call time, never at import time.

    Args:
        soundfont_path: Absolute path to a .sf2 soundfont file.

    Returns:
        A ``FluidSynthBackend`` satisfying the ``render_pcm`` backend interface
        (``note_on`` / ``note_off`` / ``render_block``).
    """
    return FluidSynthBackend(soundfont_path)
