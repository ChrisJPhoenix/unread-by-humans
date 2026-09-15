import test from 'node:test';
import assert from 'node:assert/strict';
import { shouldReload } from './reload.mjs';

// ── 1. heartbeat never triggers a reload ─────────────────────────────────────
test('shouldReload: heartbeat returns false', () => {
  assert.equal(shouldReload('abc', 'heartbeat'), false);
});

// ── 2. same nonce means same server instance, no reload ──────────────────────
test('shouldReload: same nonce returns false', () => {
  assert.equal(shouldReload('abc', 'abc'), false);
});

// ── 3. different nonce means server restarted, trigger reload ─────────────────
test('shouldReload: different nonce returns true', () => {
  assert.equal(shouldReload('abc', 'def'), true);
});
