"""Standalone repro CLI for the `/music/play` FluidSynth segfault.

This script is untested by design: it drives the REAL FluidSynth backend
(`make_fluidsynth_backend`), which is absent from the test sandbox, so it
cannot run under `bin/dev test` and is deliberately NOT imported by the
`music_render` package or any test plan. It exists so a user with a working
native FluidSynth install can reproduce the crash outside (or inside) Flask
and localize it (e.g. via `faulthandler`'s native traceback on segfault).

Modes:
  (default)            render one middle-C piano note on the main thread.
  --thread             run the chosen work on a spawned worker thread.
  --thread --big-stack same, but set a 64 MiB thread stack first.
  --bundle --text-file PATH
                       replicate the /music/play RENDER inputs: read the score
                       text from PATH and appdata/webmusicdata/instruments.json, build
                       the bundle via play_render_bundle, then render directly.
  --server-app --text-file PATH
                       replicate the /music/play ROUTE end-to-end through the
                       fully-assembled server app (every blueprint imported, so
                       every other app's native libs are loaded, exactly like the
                       running server) via an in-process Flask test client.
                       Combine with --thread to also run it on a worker thread —
                       the closest single-process match to the live server.

Run it via `bin/music_probe`. The handoff document with the background on the
segfault this reproduces is not published in this repository.
"""

import argparse
import faulthandler
import json
import os
import sys
import threading

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from libs.music_render import render_song_pcm, make_fluidsynth_backend
from libs.music_web_instruments import play_render_bundle

OUT_PATH = os.path.join(PROJECT_ROOT, "tmp", "probe_render.f32")


def _minimal_inputs() -> dict:
    """Return render inputs for a single middle-C piano note (no CCs/automation)."""
    single_middle_c_event = {"midi_note": 60, "velocity": 100, "channel": 0, "time": 0.0, "duration": 0.5}
    sections = [{"events": [single_middle_c_event]}]
    return {
        "score_json": json.dumps(sections),
        "programs_json": '{"0": [0, 0]}',
        "channel_ccs": None,
        "channel_automation": None,
    }


def _bundle_inputs(text_path: str) -> dict:
    """Replicate the /music/play route's render inputs from a score-text file.

    Reads the score text from ``text_path`` and appdata/webmusicdata/instruments.json
    (mirroring the route), builds the render bundle via ``play_render_bundle``,
    prints a summary of what it contains, and returns the four render inputs.
    """
    with open(text_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    instruments_path = os.path.join(PROJECT_ROOT, "appdata", "webmusicdata", "instruments.json")
    if os.path.isfile(instruments_path):
        with open(instruments_path, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    else:
        rows = []
    bundle = json.loads(play_render_bundle(text, 0, json.dumps(rows)))
    n_events = sum(len(sec.get("events", [])) for sec in bundle["score"])
    print(f"bundle: programs={bundle['programs']}")
    print(f"bundle: channel_ccs channels={sorted(bundle['channel_ccs'].keys())}")
    print(f"bundle: channel_automation channels={sorted(bundle['channel_automation'].keys())}")
    print(f"bundle: total note events={n_events}")
    return {
        "score_json": json.dumps(bundle["score"]),
        "programs_json": json.dumps(bundle["programs"]),
        "channel_ccs": bundle["channel_ccs"],
        "channel_automation": bundle["channel_automation"],
    }


def _render_and_report(inputs: dict) -> None:
    """Render the given inputs through the real FluidSynth backend; write PCM and print stats."""
    pcm = render_song_pcm(
        score_json=inputs["score_json"],
        instruments_json="{}",
        channel_ccs=inputs["channel_ccs"],
        channel_automation=inputs["channel_automation"],
        programs_json=inputs["programs_json"],
        backend=make_fluidsynth_backend(),
    )
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "wb") as fh:
        fh.write(pcm.astype("float32").tobytes())
    print(f"frames: {pcm.shape[0]}")
    if pcm.shape[0] == 0:
        print("(empty render — no note events in the input)")
        print(f"wrote: {OUT_PATH}")
        return
    rms = float(np.sqrt(np.mean(np.square(pcm.astype("float64")))))
    print(f"min: {float(pcm.min())}")
    print(f"max: {float(pcm.max())}")
    print(f"rms: {rms}")
    print(f"wrote: {OUT_PATH}")


def _server_app_request(text_path: str) -> None:
    """Replicate /music/play end-to-end through the fully-assembled server app.

    Imports ``create_app`` from ``web.server`` (which imports every blueprint, so
    every other app's native libraries are loaded into this process, just like the
    running server), builds the app, and POSTs the score text to /music/play via an
    in-process Flask test client. Writes the returned PCM bytes and prints the
    HTTP status and byte count.
    """
    from web.server import create_app
    with open(text_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    app = create_app()
    client = app.test_client()
    print("posting to /music/play via in-process test client (full server app)...")
    resp = client.post("/music/play", json={"text": text, "caret": 0})
    print(f"status: {resp.status_code}")
    data = resp.get_data()
    print(f"response bytes: {len(data)}")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "wb") as fh:
        fh.write(data)
    print(f"wrote: {OUT_PATH}")


def main() -> None:
    """Parse flags and reproduce the /music render in the selected mode."""
    faulthandler.enable()
    parser = argparse.ArgumentParser(description="Standalone FluidSynth render repro for the /music/play segfault.")
    parser.add_argument("--thread", action="store_true", help="run the chosen work on a spawned worker thread instead of the main thread")
    parser.add_argument("--big-stack", action="store_true", help="with --thread, set a 64 MiB thread stack size before spawning")
    parser.add_argument("--bundle", action="store_true", help="replicate /music/play render inputs from --text-file + appdata/webmusicdata/instruments.json")
    parser.add_argument("--server-app", action="store_true", help="replicate the /music/play route through the fully-assembled server app (in-process test client)")
    parser.add_argument("--text-file", help="path to a score-text file (required with --bundle or --server-app)")
    args = parser.parse_args()

    if args.server_app:
        if not args.text_file:
            parser.error("--server-app requires --text-file PATH")
        text_file = args.text_file

        def work() -> None:
            _server_app_request(text_file)
    elif args.bundle:
        if not args.text_file:
            parser.error("--bundle requires --text-file PATH")
        inputs = _bundle_inputs(args.text_file)

        def work() -> None:
            _render_and_report(inputs)
    else:
        inputs = _minimal_inputs()

        def work() -> None:
            _render_and_report(inputs)

    if args.thread:
        if args.big_stack:
            threading.stack_size(64 * 1024 * 1024)
        print(f"running on a worker thread (big_stack={args.big_stack})...")
        t = threading.Thread(target=work)
        t.start()
        t.join()
    else:
        work()


if __name__ == "__main__":
    main()
