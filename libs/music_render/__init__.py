"""music_render — offline audio render pipeline for the music web app.

Public API
----------

Schedule / PCM layer (``schedule.py``):
    build_render_schedule(score_json, sample_rate)
        Build a sample-accurate render schedule from a parsed score.
    fold_automation_into_schedule(schedule_json, automation_json)
        Merge CC / pitch-bend automation into a render schedule.
    render_pcm(schedule_json, backend, sample_rate)
        Drive a synth backend to produce stereo float32 PCM.
    render_pcm_with_fake_backend(schedule_json, sample_rate)
        Convenience: render with a fresh FakeBackend (deterministic, no deps).
    render_and_report_channels(schedule_json, sample_rate)
        Render over a fresh FakeBackend; return JSON list of channels that received a note-on.
    render_song_pcm(score_json, instruments_json, ...)
        Full-song wiring: parse output + designed instruments → schedule → render.
    render_audition_pcm(section_score_json, instruments_json, ...)
        Short audition wiring: same pipeline, shorter clip.
    encode_flac(pcm, sample_rate, path)
        Encode float32 PCM to a 24-bit FLAC file via soundfile.

Backend layer (``backend.py``):
    FakeBackend
        Deterministic synth backend (no external dependencies).
        Used as the reference implementation in tests.
    fake_backend_render_note(note, n_frames)
        Convenience: render one note via FakeBackend.
    FluidSynthBackend
        Thin glue: float/48 kHz/FLUID_INTERP_HIGHEST/warm soundfont pool.
        Untested by design (requires native fluidsynth + soundfont).
    make_fluidsynth_backend(soundfont_path)
        Factory for FluidSynthBackend; fluidsynth import is lazy.
"""
from libs.music_render.schedule import (
    build_render_schedule,
    fold_automation_into_schedule,
    render_pcm,
    render_pcm_with_fake_backend,
    render_and_report_channels,
    render_song_pcm,
    render_audition_pcm,
    encode_flac,
    render_song_and_report_setup,
)
from libs.music_render.backend import (
    FakeBackend,
    fake_backend_render_note,
    FluidSynthBackend,
    make_fluidsynth_backend,
)

__all__ = [
    "build_render_schedule",
    "fold_automation_into_schedule",
    "render_pcm",
    "render_pcm_with_fake_backend",
    "render_and_report_channels",
    "render_song_pcm",
    "render_audition_pcm",
    "encode_flac",
    "render_song_and_report_setup",
    "FakeBackend",
    "fake_backend_render_note",
    "FluidSynthBackend",
    "make_fluidsynth_backend",
]
