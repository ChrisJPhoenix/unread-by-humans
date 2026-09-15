/**
 * Boring (deterministic, DOM-free) helpers for the error-overlay pipeline.
 *
 * formatReport  — build a copy-paste-ready fenced block from a crash report.
 * stormKey      — fingerprint a report by type + stack frames (not message).
 * shouldEmit    — decide whether a new report should surface to the user.
 * crashEnvelope — unwrap the reserved __crash__ key from a JSON response body.
 */

function stackText(stack) {
  if (!stack) return '';
  if (typeof stack === 'string') return stack;
  if (Array.isArray(stack)) {
    return stack.map(f => `  at ${f.file}:${f.line}`).join('\n');
  }
  return String(stack);
}

export function formatReport(report) {
  const lines = [];
  lines.push(`Error: ${report.message ?? '(no message)'}`);
  const st = stackText(report.stack);
  if (st) lines.push(st);
  if (report.stack && Array.isArray(report.stack) && report.stack.length > 0) {
    const first = report.stack[0];
    lines.push(`Location: ${first.file}:${first.line}`);
  }
  if (report.path !== undefined) lines.push(`Request path: ${report.path}`);
  if (report.payload !== undefined) lines.push(`Payload: ${JSON.stringify(report.payload)}`);
  if (report.url !== undefined) lines.push(`Page URL: ${report.url}`);
  if (report.time !== undefined) lines.push(`Time: ${report.time}`);
  return '```\n' + lines.join('\n') + '\n```';
}

export function stormKey(report) {
  const type = report.type ?? '';
  const stack = report.stack;
  if (Array.isArray(stack)) {
    const frames = stack.map(f => `${f.file}:${f.line}`).join('|');
    return `${type}|${frames}`;
  }
  return `${type}|${typeof stack === 'string' ? stack : ''}`;
}

export function shouldEmit(key, seenKeys, distinctCount, CAP = 10) {
  if (seenKeys.has(key)) return false;
  if (distinctCount >= CAP) return false;
  return true;
}

export function crashEnvelope(jsonBody) {
  const inner = jsonBody['__crash__'];
  return inner ? inner : null;
}

// --- browser-only glue (untested) ---
if (typeof window !== 'undefined') {
  // Signal to the inline bootstrap that the rich overlay is ready; its
  // error/unhandledrejection handlers will early-return once this is set.
  window.__errorOverlayReady = true;

  // Module-level storm-suppression state (reset naturally on page reload).
  const seenKeys = new Set();
  let distinctCount = 0;
  let capNoticedShown = false;

  // Loop guard: prevents an error thrown WHILE showing the overlay from
  // recursing into itself.
  let LOOP_GUARD = false;

  // ---------------------------------------------------------------------------
  // Plain-text fallback (zero-dependency, no dialog required).
  // Dropped synchronously into document.body; upgraded by showRichDialog once
  // the DOM is available.
  // ---------------------------------------------------------------------------
  let fallbackPre = null;

  function showFallback(text) {
    if (typeof document === 'undefined') return;
    if (!fallbackPre) {
      fallbackPre = document.createElement('pre');
      fallbackPre.id = '__error_overlay_fallback__';
      fallbackPre.style.cssText = (
        'position:fixed;top:0;left:0;right:0;z-index:999999;'
        + 'background:#1a0000;color:#ff8080;padding:16px;'
        + 'font:13px/1.5 monospace;white-space:pre-wrap;'
        + 'max-height:50vh;overflow:auto;border-bottom:2px solid #ff4040;'
      );
      const target = document.body || document.documentElement;
      if (target) target.prepend(fallbackPre);
    }
    fallbackPre.textContent += text + '\n\n';
  }

  // ---------------------------------------------------------------------------
  // Rich dialog
  // ---------------------------------------------------------------------------
  function showRichDialog(report) {
    if (typeof document === 'undefined') { showFallback(formatReport(report)); return; }

    // Remove or upgrade the fallback pre once the rich dialog is live.
    if (fallbackPre && fallbackPre.parentNode) {
      fallbackPre.parentNode.removeChild(fallbackPre);
      fallbackPre = null;
    }
    // Also remove the inline-bootstrap pre (created before the module loaded).
    const inlinePre = document.getElementById('__error_overlay_fallback__');
    if (inlinePre && inlinePre.parentNode) {
      inlinePre.parentNode.removeChild(inlinePre);
    }

    const overlay = document.createElement('div');
    overlay.style.cssText = (
      'position:fixed;top:0;left:0;right:0;z-index:999999;'
      + 'background:#1a0000;color:#ff8080;padding:16px;'
      + 'font:13px/1.5 monospace;'
      + 'max-height:50vh;overflow:auto;border-bottom:2px solid #ff4040;'
    );

    const pre = document.createElement('pre');
    pre.style.cssText = 'margin:0;white-space:pre-wrap;';
    pre.textContent = formatReport(report);
    overlay.appendChild(pre);

    const copyBtn = document.createElement('button');
    copyBtn.textContent = 'Copy';
    copyBtn.style.cssText = (
      'margin-top:8px;padding:4px 12px;background:#400000;color:#ff8080;'
      + 'border:1px solid #ff4040;cursor:pointer;font:inherit;'
    );
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(formatReport(report)).catch(() => {});
    });
    overlay.appendChild(copyBtn);

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '×';
    closeBtn.style.cssText = (
      'position:absolute;top:8px;right:12px;background:none;'
      + 'border:none;color:#ff8080;font-size:18px;cursor:pointer;'
    );
    closeBtn.addEventListener('click', () => {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });
    overlay.appendChild(closeBtn);

    const target = document.body || document.documentElement;
    if (target) target.prepend(overlay);
  }

  // ---------------------------------------------------------------------------
  // Core: decide whether to surface a report, then show it.
  // ---------------------------------------------------------------------------
  function maybeShow(report) {
    if (LOOP_GUARD) return;
    LOOP_GUARD = true;
    try {
      const key = stormKey(report);
      if (!shouldEmit(key, seenKeys, distinctCount)) {
        if (!capNoticedShown) {
          capNoticedShown = true;
          const suppressed = distinctCount;
          showRichDialog({ type: 'storm', message: `… limit reached — ${suppressed} more suppressed`, stack: '', time: new Date().toISOString() });
        }
        return;
      }
      seenKeys.add(key);
      distinctCount += 1;
      showRichDialog(report);

      // Best-effort POST to /_client_error (endpoint may not exist yet).
      try {
        const body = JSON.stringify(report);
        _rawFetch('/_client_error', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      } catch (_) { /* silently ignore */ }
    } finally {
      LOOP_GUARD = false;
    }
  }

  // ---------------------------------------------------------------------------
  // window error hooks (sources 1 & 2)
  // ---------------------------------------------------------------------------
  window.addEventListener('error', (ev) => {
    const report = {
      type: 'js-error',
      message: ev.message ?? String(ev.error),
      stack: (ev.error && ev.error.stack) ? ev.error.stack : '',
      url: window.location.href,
      time: new Date().toISOString(),
    };
    maybeShow(report);
  });

  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev.reason;
    const report = {
      type: 'unhandled-rejection',
      message: (reason && reason.message) ? reason.message : String(reason),
      stack: (reason && reason.stack) ? reason.stack : '',
      url: window.location.href,
      time: new Date().toISOString(),
    };
    maybeShow(report);
  });

  // ---------------------------------------------------------------------------
  // fetch wrapper (sources 3 & 4: JSON crash envelope in response bodies).
  // We keep a reference to the original fetch BEFORE wrapping so the /_client_error
  // best-effort POST can use it without looping through this wrapper.
  // ---------------------------------------------------------------------------
  const _rawFetch = window.fetch.bind(window);

  window.fetch = async function patchedFetch(input, init) {
    // Determine the request path to exclude /_client_error from inspection.
    let requestPath = '';
    try {
      if (typeof input === 'string') requestPath = input;
      else if (input && input.url) requestPath = input.url;
      else if (input instanceof URL) requestPath = input.pathname;
    } catch (_) { /* ignore */ }

    const response = await _rawFetch(input, init);

    // Never inspect or redeliver the /_client_error path itself.
    if (requestPath && requestPath.includes('/_client_error')) return response;

    // Clone the response before the real caller reads it so we can peek.
    try {
      const clone = response.clone();
      clone.json().then((body) => {
        try {
          const crash = crashEnvelope(body);
          if (crash) maybeShow(crash);
        } catch (_) { /* ignore */ }
      }).catch(() => { /* non-JSON body — normal */ });
    } catch (_) { /* clone/parse failure — ignore */ }

    return response;
  };
}
