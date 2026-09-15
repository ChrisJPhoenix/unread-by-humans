"""Smoke checks for the error-surfacing seam (Domain 2).

Covers:
  1. /_test_crash?mode=raise with a JSON Accept header returns 500 + JSON __crash__ envelope.
  2. /_test_crash?mode=raise with Accept: text/html returns 500 + HTML skeleton (not __crash__ JSON).
  3. /_test_crash?mode=abort returns 404 (deliberate HTTP status, not a crash report).
  4. A normal HTML page has /static/error_overlay.mjs injected.
"""

import json


def run(ctx, check) -> None:
    # 1. JSON caller: synthetic raise → 500 + __crash__ envelope with required fields.
    r = ctx.client.get(
        "/_test_crash?mode=raise",
        headers={"Accept": "application/json"},
    )
    check("mode=raise JSON -> 500", r.status_code == 500)
    try:
        body = json.loads(r.get_data(as_text=True))
        envelope_present = "__crash__" in body
        report = body.get("__crash__", {})
        has_type = "type" in report
        has_message = "message" in report
        has_stack = "stack" in report
        message_text = report.get("message", "")
        has_synthetic_text = "synthetic test crash" in message_text
    except (ValueError, AttributeError):
        envelope_present = has_type = has_message = has_stack = has_synthetic_text = False
    check("mode=raise JSON body has __crash__ key", envelope_present)
    check("mode=raise report has type field", has_type)
    check("mode=raise report has message field", has_message)
    check("mode=raise report has stack field", has_stack)
    check("mode=raise report message contains 'synthetic test crash'", has_synthetic_text)

    # 2. HTML caller: synthetic raise → 500 + HTML skeleton, NOT the raw __crash__ JSON envelope.
    r = ctx.client.get(
        "/_test_crash?mode=raise",
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    check("mode=raise HTML -> 500", r.status_code == 500)
    html_body = r.get_data(as_text=True)
    check("mode=raise HTML body contains 'synthetic test crash'", "synthetic test crash" in html_body)
    check("mode=raise HTML body is not raw __crash__ JSON", "__crash__" not in html_body)

    # 3. Deliberate abort(404) passes through unchanged — no crash report.
    r = ctx.client.get("/_test_crash?mode=abort")
    check("mode=abort -> 404 (HTTPException passthrough)", r.status_code == 404)
    abort_body = r.get_data(as_text=True)
    check("mode=abort body has no __crash__ key", "__crash__" not in abort_body)

    # 4. Normal HTML page has /static/error_overlay.mjs injected.
    r = ctx.client.get("/")
    body = r.get_data(as_text=True)
    check("GET / -> 200 HTML with error_overlay.mjs injected", r.status_code == 200 and "/static/error_overlay.mjs" in body)

    # 5. POST /_client_error with a valid browser-error report returns 204 (no crash).
    sample_report = {"type": "js-error", "message": "x", "stack": "a.mjs:1", "url": "http://x/", "time": "t"}
    r = ctx.client.post(
        "/_client_error",
        data=json.dumps(sample_report),
        content_type="application/json",
    )
    check("POST /_client_error valid report -> 204", r.status_code == 204)

    # 6. POST /_client_error with an invalid/empty body does not crash (returns 2xx).
    r = ctx.client.post(
        "/_client_error",
        data="not-json",
        content_type="text/plain",
    )
    check("POST /_client_error invalid body -> 2xx (no crash)", r.status_code < 300)
