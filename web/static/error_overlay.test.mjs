import test from 'node:test';
import assert from 'node:assert/strict';
import { formatReport, stormKey, shouldEmit, crashEnvelope } from './error_overlay.mjs';

// ── formatReport ─────────────────────────────────────────────────────────────

test('formatReport: output includes the message', () => {
  const out = formatReport({ message: 'Something went wrong' });
  assert.ok(out.includes('Something went wrong'));
});

test('formatReport: output is wrapped in triple-backtick fence', () => {
  const out = formatReport({ message: 'oops' });
  assert.ok(out.startsWith('```\n'));
  assert.ok(out.endsWith('\n```'));
});

test('formatReport: stack frames appear in the output', () => {
  const report = {
    message: 'boom',
    stack: [{ file: 'foo.mjs', line: 42 }, { file: 'bar.mjs', line: 7 }],
  };
  const out = formatReport(report);
  assert.ok(out.includes('foo.mjs'));
  assert.ok(out.includes('42'));
});

test('formatReport: string stack appears in the output', () => {
  const report = { message: 'boom', stack: 'Error\n  at main (app.js:1)' };
  const out = formatReport(report);
  assert.ok(out.includes('app.js'));
});

test('formatReport: does not throw when optional fields are missing', () => {
  assert.doesNotThrow(() => formatReport({ message: 'minimal' }));
  assert.doesNotThrow(() => formatReport({}));
});

test('formatReport: includes path and payload when present', () => {
  const out = formatReport({ message: 'x', path: '/api/run', payload: { n: 1 } });
  assert.ok(out.includes('/api/run'));
  assert.ok(out.includes('"n"'));
});

test('formatReport: omits path/payload/url when absent', () => {
  const out = formatReport({ message: 'x' });
  assert.ok(!out.includes('Request path:'));
  assert.ok(!out.includes('Payload:'));
  assert.ok(!out.includes('Page URL:'));
});

// ── stormKey ──────────────────────────────────────────────────────────────────

test('stormKey: same type+frames with different messages produce equal keys', () => {
  const frames = [{ file: 'a.mjs', line: 1 }];
  const k1 = stormKey({ type: 'TypeError', message: 'foo', stack: frames });
  const k2 = stormKey({ type: 'TypeError', message: 'bar', stack: frames });
  assert.equal(k1, k2);
});

test('stormKey: different frames produce different keys', () => {
  const k1 = stormKey({ type: 'Error', stack: [{ file: 'a.mjs', line: 1 }] });
  const k2 = stormKey({ type: 'Error', stack: [{ file: 'b.mjs', line: 9 }] });
  assert.notEqual(k1, k2);
});

test('stormKey: different types produce different keys even with same frames', () => {
  const frames = [{ file: 'a.mjs', line: 1 }];
  const k1 = stormKey({ type: 'TypeError', stack: frames });
  const k2 = stormKey({ type: 'RangeError', stack: frames });
  assert.notEqual(k1, k2);
});

// ── shouldEmit ────────────────────────────────────────────────────────────────

test('shouldEmit: returns false for a duplicate key', () => {
  const seen = new Set(['key-a']);
  assert.equal(shouldEmit('key-a', seen, 0), false);
});

test('shouldEmit: returns false when distinctCount >= CAP', () => {
  const seen = new Set();
  assert.equal(shouldEmit('new-key', seen, 10, 10), false);
  assert.equal(shouldEmit('new-key', seen, 11, 10), false);
});

test('shouldEmit: returns true for a new key when under CAP', () => {
  const seen = new Set(['existing']);
  assert.equal(shouldEmit('fresh-key', seen, 3, 10), true);
});

test('shouldEmit: cap applies to distinctCount, not seenKeys.size', () => {
  // distinctCount is provided externally; even an empty seenKeys is blocked if count is at CAP
  const seen = new Set();
  assert.equal(shouldEmit('brand-new', seen, 10), false);
});

// ── crashEnvelope ─────────────────────────────────────────────────────────────

test('crashEnvelope: returns the inner report for a __crash__ body', () => {
  const inner = { message: 'server exploded' };
  const result = crashEnvelope({ '__crash__': inner });
  assert.deepEqual(result, inner);
});

test('crashEnvelope: returns null for a normal body without __crash__', () => {
  assert.equal(crashEnvelope({ result: 'ok' }), null);
  assert.equal(crashEnvelope({}), null);
});
