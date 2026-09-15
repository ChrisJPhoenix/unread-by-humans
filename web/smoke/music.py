"""Smoke checks for the /music app routes (Domain 2)."""

import json

from libs.music_render import (
    FakeBackend,
    render_audition_pcm,
    render_song_pcm,
)
from libs.music_web_instruments import audition_score_and_programs, play_render_bundle
from web.apps.music import _DEFAULT_SAMPLE_RATE

# ── Shared fixtures ────────────────────────────────────────────────────────────

_INSTRUMENT_ROWS = [
    {
        "name": "Piano",
        "bank": 0,
        "program": 0,
        "is_percussion": False,
        "drum_note": None,
        "volume": 100,
        "pan": 64,
    }
]

_MINIMAL_SCORE = "piano:c4 c d e"

_AUDITION_SPEC = {
    "bank": 0,
    "program": 40,
    "is_percussion": False,
    "drum_note": None,
    "pitch": "C4",
    "velocity": 100,
    "duration_s": 1.0,
    "volume": 100,
    "pan": 64,
    "reverb": 40,
    "chorus": 0,
}


def seed(ctx) -> None:
    """Write a appdata/webmusicdata/instruments.json fixture to the real project root.

    The real client uses the actual project root on disk; we POST the fixture
    so subsequent smoke checks see a populated instruments file.  The temp
    client root gets its own copy for the path-traversal check.
    """
    ctx.real_client.post(
        "/music/instruments",
        data=json.dumps(_INSTRUMENT_ROWS),
        content_type="application/json",
    )


# ── Expected byte lengths (derived from the same render path) ─────────────────

def _expected_audition_pcm_byte_length() -> int:
    """Return expected raw PCM byte count for the audition clip via FakeBackend."""
    bundle = json.loads(audition_score_and_programs(json.dumps(_AUDITION_SPEC)))
    pcm = render_audition_pcm(
        section_score_json=json.dumps(bundle["score"]),
        instruments_json=json.dumps(bundle.get("instruments", {})),
        channel_ccs=bundle["channel_ccs"],
        channel_automation=bundle["channel_automation"],
        programs_json=json.dumps(bundle["programs"]),
        backend=FakeBackend(),
    )
    return pcm.astype("float32").nbytes


def _expected_play_pcm_byte_length() -> int:
    """Return expected raw PCM byte count for the play route via FakeBackend."""
    bundle = json.loads(play_render_bundle("piano:c4 c d e", 0, json.dumps(_INSTRUMENT_ROWS)))
    pcm = render_song_pcm(
        score_json=json.dumps(bundle["score"]),
        instruments_json="{}",
        channel_ccs=bundle["channel_ccs"],
        channel_automation=bundle["channel_automation"],
        programs_json=json.dumps(bundle["programs"]),
        backend=FakeBackend(),
    )
    return pcm.astype("float32").nbytes


def run(ctx, check) -> None:
    # ── GET /music -> 200 (music app page shell) ──────────────────────────────
    r = ctx.real_client.get("/music")
    check("GET /music -> 200", r.status_code == 200)

    # ── GET /music/instruments -> 200, JSON list ──────────────────────────────
    r = ctx.real_client.get("/music/instruments")
    check("GET /music/instruments -> 200", r.status_code == 200)
    data = r.get_json()
    check("GET /music/instruments -> list", isinstance(data, list))

    # ── POST /music/instruments round-trip ────────────────────────────────────
    r = ctx.real_client.post(
        "/music/instruments",
        data=json.dumps(_INSTRUMENT_ROWS),
        content_type="application/json",
    )
    check("POST /music/instruments -> ok", r.status_code == 200 and r.get_json().get("ok") is True)

    r = ctx.real_client.get("/music/instruments")
    stored = r.get_json()
    check(
        "POST /music/instruments round-trips correctly",
        isinstance(stored, list) and len(stored) == 1 and stored[0].get("name") == "Piano",
    )

    # ── /music/instruments path-traversal guard (POST with no path param; test
    #    via the instruments_path helper: the stored path is always fixed, so we
    #    verify that a bad body returns 400 not 500) ───────────────────────────
    r = ctx.real_client.post(
        "/music/instruments",
        data="not-json",
        content_type="text/plain",
    )
    check("POST /music/instruments bad body -> 400", r.status_code == 400)

    # ── POST /music/parse with a known small score ────────────────────────────
    r = ctx.real_client.post(
        "/music/parse",
        data=json.dumps({"text": _MINIMAL_SCORE}),
        content_type="application/json",
    )
    check("POST /music/parse -> 200", r.status_code == 200)
    data = r.get_json()
    check(
        "POST /music/parse -> has sections + beat_counts + instrument_channels",
        (
            isinstance(data, dict)
            and "sections" in data
            and "beat_counts" in data
            and "instrument_channels" in data
        ),
    )
    check(
        "POST /music/parse -> sections has events",
        any(len(sec.get("events", [])) > 0 for sec in data["sections"].values()),
    )

    # ── POST /music/parse with deliberately bad text -> errors surfaced, not 500
    r = ctx.real_client.post(
        "/music/parse",
        data=json.dumps({"text": "unknowninstrument:c4 x y z"}),
        content_type="application/json",
    )
    check(
        "POST /music/parse bad text -> 200 or 400, never 500",
        r.status_code in (200, 400),
    )
    bad_data = r.get_json()
    check(
        "POST /music/parse bad text -> errors surfaced in JSON",
        isinstance(bad_data, dict)
        and (
            bad_data.get("errors")
            or any(
                len(sec.get("errors", [])) > 0
                for sec in bad_data.get("sections", {}).values()
            )
        ),
    )

    # ── POST /music/parse missing body -> 400 ────────────────────────────────
    r = ctx.real_client.post(
        "/music/parse",
        data="",
        content_type="application/json",
    )
    check("POST /music/parse missing body -> 400", r.status_code == 400)

    # ── POST /music/audition?backend=fake -> raw PCM bytes ───────────────────
    expected_audition_bytes = _expected_audition_pcm_byte_length()
    r = ctx.real_client.post(
        "/music/audition?backend=fake",
        data=json.dumps(_AUDITION_SPEC),
        content_type="application/json",
    )
    check("POST /music/audition?backend=fake -> 200", r.status_code == 200)
    check(
        "POST /music/audition?backend=fake -> octet-stream",
        "octet-stream" in r.content_type,
    )
    check(
        "POST /music/audition?backend=fake -> correct PCM byte length",
        len(r.data) == expected_audition_bytes,
    )

    # ── POST /music/audition missing body -> 400 ─────────────────────────────
    r = ctx.real_client.post(
        "/music/audition?backend=fake",
        data="not-json",
        content_type="text/plain",
    )
    check("POST /music/audition bad body -> 400", r.status_code == 400)

    # ── POST /music/play?backend=fake -> raw PCM bytes ───────────────────────
    expected_play_bytes = _expected_play_pcm_byte_length()
    r = ctx.real_client.post(
        "/music/play?backend=fake",
        data=json.dumps({"text": "piano:c4 c d e", "caret": 0}),
        content_type="application/json",
    )
    check("POST /music/play?backend=fake -> 200", r.status_code == 200)
    check(
        "POST /music/play?backend=fake -> octet-stream",
        "octet-stream" in r.content_type,
    )
    check(
        "POST /music/play?backend=fake -> correct PCM byte length",
        len(r.data) == expected_play_bytes,
    )

    # ── POST /music/play missing body -> 400 ─────────────────────────────────
    r = ctx.real_client.post(
        "/music/play?backend=fake",
        data="",
        content_type="application/json",
    )
    check("POST /music/play bad body -> 400", r.status_code == 400)

    # ── File routes: scores list / new / get / put / session ─────────────────
    # Use the temp-root client (ctx.client) so created score files land in the
    # smoke temp dir (cleaned up by _harness) and never pollute real appdata/webmusicdata/.
    fc = ctx.client

    # new (name required) creates the file; it is reported and set current
    r = fc.post(
        "/music/scores/new",
        data=json.dumps({"name": "smoke.music"}),
        content_type="application/json",
    )
    check(
        "POST /music/scores/new -> 200 with name",
        r.status_code == 200 and r.get_json().get("name") == "smoke.music",
    )

    r = fc.get("/music/scores")
    listing = r.get_json()
    check(
        "GET /music/scores -> smoke.music listed",
        r.status_code == 200 and "smoke.music" in listing.get("files", []),
    )
    check("GET /music/scores -> current is smoke.music", listing.get("current") == "smoke.music")

    # new with no name -> 400
    r = fc.post("/music/scores/new", data=json.dumps({}), content_type="application/json")
    check("POST /music/scores/new no name -> 400", r.status_code == 400)

    # new colliding name -> 409
    r = fc.post(
        "/music/scores/new",
        data=json.dumps({"name": "smoke.music"}),
        content_type="application/json",
    )
    check("POST /music/scores/new collision -> 409", r.status_code == 409)

    # PUT round-trips through GET
    r = fc.put(
        "/music/score",
        data=json.dumps({"name": "smoke.music", "text": "piano:c4 d e"}),
        content_type="application/json",
    )
    check("PUT /music/score -> ok", r.status_code == 200 and r.get_json().get("ok") is True)
    r = fc.get("/music/score?name=smoke.music")
    check(
        "GET /music/score round-trips text",
        r.status_code == 200 and r.get_json().get("text") == "piano:c4 d e",
    )

    # traversal / subpath names -> 400
    r = fc.get("/music/score?name=../etc/passwd")
    check("GET /music/score traversal -> 400", r.status_code == 400)
    r = fc.get("/music/score?name=a/b")
    check("GET /music/score subpath -> 400", r.status_code == 400)

    # missing file -> 404
    r = fc.get("/music/score?name=nope.music")
    check("GET /music/score missing -> 404", r.status_code == 404)

    # session: switch to a missing file -> 404
    r = fc.post(
        "/music/session",
        data=json.dumps({"current": "nope.music"}),
        content_type="application/json",
    )
    check("POST /music/session missing current -> 404", r.status_code == 404)

    # session: caret/scroll persist and echo back through GET /music/scores
    r = fc.post(
        "/music/session",
        data=json.dumps({"caret": 12, "scroll": 34}),
        content_type="application/json",
    )
    check("POST /music/session caret/scroll -> ok", r.status_code == 200)
    r = fc.get("/music/scores")
    echoed = r.get_json()
    check(
        "GET /music/scores echoes caret/scroll",
        echoed.get("caret") == 12 and echoed.get("scroll") == 34,
    )

    # bad JSON body on PUT -> 400 (never 500)
    r = fc.put("/music/score", data="not-json", content_type="text/plain")
    check("PUT /music/score bad body -> 400", r.status_code == 400)
