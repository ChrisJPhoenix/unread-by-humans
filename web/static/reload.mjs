/**
 * Decide whether to reload: the SSE stream sends the server's per-process nonce
 * first, then "heartbeat" lines. A nonce different from the first one we saw means
 * the server restarted, so the page should reload.
 */
export function shouldReload(firstNonce, message) {
  if (message === 'heartbeat') return false;
  if (message === firstNonce) return false;
  return true;
}

// --- browser-only glue (untested) ---
if (typeof EventSource !== 'undefined') {
  let firstNonce = null;
  const es = new EventSource('/sse-reload');
  es.onmessage = (e) => {
    const message = e.data;
    if (message === 'heartbeat') return;
    if (firstNonce === null) { firstNonce = message; return; }
    if (shouldReload(firstNonce, message)) location.reload();
  };
}
