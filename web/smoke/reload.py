"""Smoke checks for the GET / app index and GET /sse-reload nonce-based auto-reload (Domain 2).

SSE-hang gotcha: the /sse-reload generator is an infinite loop. Never consume
the whole body via r.data or iteration without limiting — the test hangs forever.
Always read exactly one chunk via next(iter(r.response)) and then close.
"""


def run(ctx, check) -> None:
    # 1. GET / returns 200 with app index containing /music link.
    r = ctx.client.get("/")
    body = r.get_data(as_text=True)
    check("GET / -> 200 app index", r.status_code == 200 and "/music" in body)
    check("reload client injected into HTML", "/static/reload.mjs" in body)
    check(
        "non-app page injects window.__APP_ROUTE__ = null",
        "window.__APP_ROUTE__ = null;" in body,
    )

    # 2. App page injects its own route.
    r = ctx.real_client.get("/music")
    body = r.get_data(as_text=True)
    check(
        "app page injects window.__APP_ROUTE__ = \"/music\"",
        'window.__APP_ROUTE__ = "/music";' in body,
    )

    # 3. SSE endpoint — read ONLY the first chunk; the stream is infinite, so consuming it fully would hang.
    r = ctx.client.get("/sse-reload", buffered=False)
    check(
        "GET /sse-reload -> 200 text/event-stream",
        r.status_code == 200 and r.mimetype == "text/event-stream",
    )
    first = next(iter(r.response))  # read only the first chunk; the stream is infinite, so consuming it fully would hang.
    check(
        "first SSE event is the server nonce (not heartbeat)",
        first.strip().startswith(b"data:") and b"heartbeat" not in first,
    )
    r.close()
