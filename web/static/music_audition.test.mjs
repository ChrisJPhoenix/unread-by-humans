'use strict';

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  frameCountFromByteLength,
  byteLengthFromFrameCount,
  deinterleave,
  fadeGainAtFrame,
  applyFadeInOut,
  nextAuditionToken,
  isCurrent,
} from './music_audition.mjs';

// ── 1. Frame ↔ byte round-trips ───────────────────────────────────────────────

test('byteLengthFromFrameCount: 10 frames stereo = 80 bytes', () => {
  assert.equal(byteLengthFromFrameCount(10, 2), 80);
});

test('byteLengthFromFrameCount: 1 frame mono = 4 bytes', () => {
  assert.equal(byteLengthFromFrameCount(1, 1), 4);
});

test('frameCountFromByteLength: 80 bytes stereo = 10 frames', () => {
  assert.equal(frameCountFromByteLength(80, 2), 10);
});

test('frameCountFromByteLength: round-trip with default channels=2', () => {
  const frames = 100;
  const bytes = byteLengthFromFrameCount(frames);
  assert.equal(frameCountFromByteLength(bytes), frames);
});

test('frameCountFromByteLength: throws on non-whole-frame byte length', () => {
  // 9 bytes is not divisible by 8 (stereo frame = 4*2 bytes)
  assert.throws(() => frameCountFromByteLength(9, 2), RangeError);
});

test('frameCountFromByteLength: throws on 1-byte input (not divisible by 8)', () => {
  assert.throws(() => frameCountFromByteLength(1, 2), RangeError);
});

// ── 2. Deinterleave ───────────────────────────────────────────────────────────

test('deinterleave: known 4-frame stereo buffer splits into exact per-channel arrays', () => {
  // L0 R0 L1 R1 L2 R2 L3 R3
  const interleaved = new Float32Array([1, 2, 3, 4, 5, 6, 7, 8]);
  const [left, right] = deinterleave(interleaved, 2);
  assert.deepEqual(Array.from(left),  [1, 3, 5, 7], 'left channel');
  assert.deepEqual(Array.from(right), [2, 4, 6, 8], 'right channel');
});

test('deinterleave: mono passthrough', () => {
  // Float32Array stores values at float32 precision, so compare within epsilon.
  const interleaved = new Float32Array([0.5, 0.25, 0.75]);
  const [ch0] = deinterleave(interleaved, 1);
  // These values are exact in float32, so strict deepEqual is safe.
  assert.deepEqual(Array.from(ch0), [0.5, 0.25, 0.75]);
});

test('deinterleave: returns array of Float32Arrays with length === frameCount', () => {
  const interleaved = new Float32Array(8); // 4 stereo frames
  const channels = deinterleave(interleaved, 2);
  assert.equal(channels.length, 2);
  assert.equal(channels[0].length, 4);
  assert.equal(channels[1].length, 4);
});

// ── 3. fadeGainAtFrame — boundary oracles ────────────────────────────────────

// Setup: 10-frame clip, 4-frame fade-in, 3-frame fade-out.
// Fade-in: frames 0–3, gain = frame/4.  Steady: frames 4–6. Fade-out: frames 7–9.
// fadeOutStart = 10 - 3 = 7.
// Frame 0:  gain = 0/4 = 0.0
// Frame 1:  gain = 1/4 = 0.25
// Frame 2:  gain = 2/4 = 0.5
// Frame 3:  gain = 3/4 = 0.75
// Frame 4:  gain = 1.0 (past fade-in, before fade-out)
// Frame 6:  gain = 1.0 (still steady)
// Frame 7:  gain = 1 - 0/3 = 1.0  (start of fade-out, framesIntoFadeOut=0)
// Frame 8:  gain = 1 - 1/3 ≈ 0.6667
// Frame 9:  gain = 1 - 2/3 ≈ 0.3333

test('fadeGainAtFrame: frame 0 of fade-in → 0', () => {
  assert.equal(fadeGainAtFrame(0, 10, 4, 3), 0);
});

test('fadeGainAtFrame: end of fade-in (frame 4) → 1', () => {
  // frame 4 is the first frame past the 4-frame fade-in ramp → gain = 1
  assert.equal(fadeGainAtFrame(4, 10, 4, 3), 1);
});

test('fadeGainAtFrame: mid-ramp (frame 2 of 4-frame fade-in) → 0.5', () => {
  const gain = fadeGainAtFrame(2, 10, 4, 3);
  assert.ok(Math.abs(gain - 0.5) < 1e-12, `expected 0.5, got ${gain}`);
});

test('fadeGainAtFrame: mid-ramp (frame 1 of 4-frame fade-in) → 0.25', () => {
  const gain = fadeGainAtFrame(1, 10, 4, 3);
  assert.ok(Math.abs(gain - 0.25) < 1e-12, `expected 0.25, got ${gain}`);
});

test('fadeGainAtFrame: steady middle (frame 6 of 10, 4-in/3-out) → 1', () => {
  assert.equal(fadeGainAtFrame(6, 10, 4, 3), 1);
});

test('fadeGainAtFrame: last frame of fade-out (frame 9 of 10, 3-out) → ~0.333', () => {
  // framesIntoFadeOut = 9 - 7 = 2; gain = 1 - 2/3
  const gain = fadeGainAtFrame(9, 10, 4, 3);
  assert.ok(Math.abs(gain - (1 - 2 / 3)) < 1e-12, `expected ${1 - 2/3}, got ${gain}`);
});

test('fadeGainAtFrame: first frame of fade-out (frame 7 of 10, 3-out) → 1.0', () => {
  // framesIntoFadeOut = 0; gain = 1 - 0/3 = 1
  assert.equal(fadeGainAtFrame(7, 10, 4, 3), 1);
});

test('fadeGainAtFrame: no fades → always 1', () => {
  assert.equal(fadeGainAtFrame(0, 10, 0, 0), 1);
  assert.equal(fadeGainAtFrame(5, 10, 0, 0), 1);
  assert.equal(fadeGainAtFrame(9, 10, 0, 0), 1);
});

// ── 4. applyFadeInOut — constant-buffer oracle ────────────────────────────────

test('applyFadeInOut: constant 1.0 buffer with 2-frame fade-in/out over 6 frames', () => {
  // 6 frames, fade-in=2, fade-out=2; fadeOutStart = 4.
  // Gains: [0/2, 1/2, 1, 1, 1-0/2, 1-1/2] = [0, 0.5, 1, 1, 1, 0.5]
  const ch = new Float32Array([1, 1, 1, 1, 1, 1]);
  const [out] = applyFadeInOut([ch], 2, 2);
  const expected = [0, 0.5, 1, 1, 1, 0.5];
  for (let i = 0; i < 6; i++) {
    assert.ok(Math.abs(out[i] - expected[i]) < 1e-12,
      `frame ${i}: expected ${expected[i]}, got ${out[i]}`);
  }
});

test('applyFadeInOut: does not mutate input channel arrays', () => {
  const ch = new Float32Array([1, 1, 1, 1]);
  const copy = Array.from(ch);
  applyFadeInOut([ch], 1, 1);
  assert.deepEqual(Array.from(ch), copy, 'input was mutated');
});

test('applyFadeInOut: applies to both channels independently', () => {
  const left  = new Float32Array([2, 2, 2, 2]);
  const right = new Float32Array([4, 4, 4, 4]);
  // 4 frames, fade-in=2, fade-out=2; fadeOutStart=2.
  // Gains: [0/2, 1/2, 1-0/2, 1-1/2] = [0, 0.5, 1, 0.5]
  const [outL, outR] = applyFadeInOut([left, right], 2, 2);
  const expectedL = [0, 1, 2, 1];
  const expectedR = [0, 2, 4, 2];
  for (let i = 0; i < 4; i++) {
    assert.ok(Math.abs(outL[i] - expectedL[i]) < 1e-12,
      `left[${i}]: expected ${expectedL[i]}, got ${outL[i]}`);
    assert.ok(Math.abs(outR[i] - expectedR[i]) < 1e-12,
      `right[${i}]: expected ${expectedR[i]}, got ${outR[i]}`);
  }
});

// ── 5. Cancel/replace bookkeeping ────────────────────────────────────────────

test('nextAuditionToken: monotonically increases from 0', () => {
  let token = 0;
  token = nextAuditionToken(token);
  assert.equal(token, 1);
  token = nextAuditionToken(token);
  assert.equal(token, 2);
  token = nextAuditionToken(token);
  assert.equal(token, 3);
});

test('isCurrent: stale token → false', () => {
  const latest = nextAuditionToken(nextAuditionToken(0)); // 2
  assert.equal(isCurrent(1, latest), false, 'stale token should be false');
});

test('isCurrent: latest token → true', () => {
  let token = 0;
  token = nextAuditionToken(token);
  assert.equal(isCurrent(token, token), true, 'current token should be true');
});

test('isCurrent: after two advances, only the last is current', () => {
  let t = 0;
  const t1 = nextAuditionToken(t);
  const t2 = nextAuditionToken(t1);
  assert.equal(isCurrent(t1, t2), false, 't1 is stale after t2');
  assert.equal(isCurrent(t2, t2), true,  't2 is current');
});
