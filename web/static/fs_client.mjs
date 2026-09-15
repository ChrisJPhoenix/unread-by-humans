async function request(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).error || ''; } catch (_) { detail = await res.text().catch(() => ''); }
    throw new Error(`fs request failed (${res.status}): ${detail}`);
  }
  return res;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const fs = {
  async read(path) {
    const res = await request('/fs/read?path=' + encodeURIComponent(path));
    return res.text();
  },
  async write(path, contents, opts = {}) {
    const res = await request('/fs/write', { method: 'POST', headers: JSON_HEADERS,
      body: JSON.stringify({ path, contents, noOverwrite: opts.noOverwrite }) });
    return res.json();
  },
  async list(path) {
    const res = await request('/fs/list?path=' + encodeURIComponent(path));
    return res.json();
  },
  async mkdir(path) {
    const res = await request('/fs/mkdir', { method: 'POST', headers: JSON_HEADERS,
      body: JSON.stringify({ path }) });
    return res.json();
  },
};

if (typeof window !== 'undefined') { window.fs = fs; }
