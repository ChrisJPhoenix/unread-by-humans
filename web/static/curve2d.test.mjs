import test from 'node:test';
import assert from 'node:assert/strict';
import { Curve2D, sampleCurve, applyEasing } from './curve2d.mjs';

// Helper: build a minimal straight-line curve for oracle/invariant tests.
function makeStraightCurve() {
  return new Curve2D({
    valueMin: 0, valueMax: 100,
    valueMinLabel: '', valueMaxLabel: '',
    timeLeftLabel: '', timeRightLabel: '',
    initialLeftY: 0, initialRightY: 100,
    id: 'fix',
  });
}

// ── 1. Sample oracle ──────────────────────────────────────────────────────────
test('sample oracle: straight line 0->100', () => {
  const c = makeStraightCurve();
  assert.ok(Math.abs(c.sample(0)   -   0) < 1e-9, 'sample(0)≈0');
  assert.ok(Math.abs(c.sample(0.5) -  50) < 1e-9, 'sample(0.5)≈50');
  assert.ok(Math.abs(c.sample(1)   - 100) < 1e-9, 'sample(1)≈100');
});

// ── 2. Construction invariants ────────────────────────────────────────────────
test('construction invariants: 2 points, endpoints at x=0 and x=1', () => {
  const c = makeStraightCurve();
  assert.equal(c.points.length, 2, 'starts with exactly 2 points');
  assert.equal(c.points[0][0], 0, 'points[0].x === 0');
  assert.equal(c.points[c.points.length - 1][0], 1, 'points[last].x === 1');
  assert.equal(c.points[0][1], 0,   'left endpoint y === initialLeftY');
  assert.equal(c.points[1][1], 100, 'right endpoint y === initialRightY');
});

// ── 3. Insert interior points ─────────────────────────────────────────────────
test('insert: three valid interior inserts yields 5 sorted points', () => {
  const c = makeStraightCurve();
  c.insert(0.25, 25);
  c.insert(0.75, 75);
  c.insert(0.5,  50);
  assert.equal(c.points.length, 5, '5 points after 3 inserts');
  // Verify ascending x order
  for (let i = 0; i < c.points.length - 1; i++) {
    assert.ok(c.points[i][0] < c.points[i+1][0],
      `points[${i}].x < points[${i+1}].x`);
  }
});

// ── 4. Insert RangeError cases ────────────────────────────────────────────────
test('insert: x=0 throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.insert(0, 50), RangeError);
});

test('insert: x=1 throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.insert(1, 50), RangeError);
});

test('insert: x=-0.1 throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.insert(-0.1, 50), RangeError);
});

test('insert: y out of range (200) throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.insert(0.5, 200), RangeError);
});

test('insert: collision (x within 1/9999 of existing point) throws RangeError', () => {
  const c = makeStraightCurve();
  c.insert(0.5, 50);
  // Insert at x within 1/9999 threshold of 0.5 — should collide
  const collideX = 0.5 + 1 / 20000; // less than 1/9999
  assert.throws(() => c.insert(collideX, 50), RangeError);
});

// ── 5. Remove ─────────────────────────────────────────────────────────────────
test('remove: remove(0) throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.remove(0), RangeError);
});

test('remove: remove(lastIndex) throws RangeError', () => {
  const c = makeStraightCurve();
  assert.throws(() => c.remove(c.points.length - 1), RangeError);
});

test('remove: valid interior remove reduces length by 1', () => {
  const c = makeStraightCurve();
  c.insert(0.5, 50);
  assert.equal(c.points.length, 3);
  c.remove(1);
  assert.equal(c.points.length, 2, 'one point removed');
});

// ── 6. Move endpoint x-clamp ──────────────────────────────────────────────────
test('move: endpoint x is clamped (left endpoint stays at x=0)', () => {
  const c = makeStraightCurve();
  // move(index, x, y) — try to move left endpoint to x=0.5
  // NOTE: move on left endpoint also y-links the right endpoint.
  // We only check that x stays at 0.
  c.move(0, 0.5, 10);
  assert.equal(c.points[0][0], 0, 'left endpoint x clamped to 0');
});

// ── 7. Equality gate: no-op move fires no change ──────────────────────────────
test('equality gate: no-op move fires no onChange', () => {
  const c = makeStraightCurve();
  let fired = 0;
  c.onChange(() => { fired++; });
  // Move left endpoint to its current position (x=0, y=0)
  c.move(0, 0, 0);
  assert.equal(fired, 0, 'no change event for no-op move');
});

// ── 8. Provenance: source and curveId in change record ───────────────────────
test('provenance: change record has correct source and curveId', () => {
  const c = makeStraightCurve();
  let record = null;
  c.onChange(r => { record = r; });
  c.move(0, 0, 50, { source: 'test' });
  assert.ok(record !== null, 'change event was fired');
  assert.equal(record.source, 'test', 'source matches');
  assert.equal(record.curveId, 'fix', 'curveId matches curve id');
  assert.equal(record.field, 'points', 'field is points');
});

// ── 9. sampleMany ─────────────────────────────────────────────────────────────
test('sampleMany: returns correct length and matches sample at endpoints', () => {
  const c = makeStraightCurve();
  const ts = [0, 0.25, 0.5, 0.75, 1];
  const vals = c.sampleMany(ts);
  assert.equal(vals.length, ts.length, 'length matches input');
  assert.ok(Math.abs(vals[0] -   0) < 1e-9, 'sampleMany at t=0 ≈ 0');
  assert.ok(Math.abs(vals[4] - 100) < 1e-9, 'sampleMany at t=1 ≈ 100');
  // Each value matches individual sample()
  for (let i = 0; i < ts.length; i++) {
    assert.ok(Math.abs(vals[i] - c.sample(ts[i])) < 1e-9,
      `sampleMany[${i}] matches sample(${ts[i]})`);
  }
});

// ── 10. toJSON / fromJSON round-trip ─────────────────────────────────────────
test('toJSON/fromJSON round-trip preserves points and sample', () => {
  const c = makeStraightCurve();
  c.insert(0.5, 50);
  const json = c.toJSON();
  const c2 = Curve2D.fromJSON(json);
  assert.equal(c2.points.length, c.points.length, 'same point count');
  // Find the interior point in c2
  const interior = c2.points.find(p => p[0] !== 0 && p[0] !== 1);
  assert.ok(interior, 'interior point exists after fromJSON');
  assert.ok(Math.abs(interior[0] - 0.5) < 1e-9, 'interior x matches');
  assert.ok(Math.abs(interior[1] - 50)  < 1e-9, 'interior y matches');
  assert.ok(Math.abs(c2.sample(0.5) - c.sample(0.5)) < 1e-9,
    'sample(0.5) matches after round-trip');
});

// ── 11. onChange unsubscribe ──────────────────────────────────────────────────
test('onChange: unsubscribe stops future notifications', () => {
  const c = makeStraightCurve();
  let fired = 0;
  const unsub = c.onChange(() => { fired++; });
  c.insert(0.3, 30);
  assert.equal(fired, 1, 'fires before unsubscribe');
  unsub();
  c.insert(0.6, 60);
  assert.equal(fired, 1, 'does not fire after unsubscribe');
});

// Helper: build a wrapY curve (-180..180, period=360) with an interior point.
function makeWrapCurve() {
  const c = new Curve2D({
    valueMin: -180, valueMax: 180,
    valueMinLabel: '', valueMaxLabel: '',
    timeLeftLabel: '', timeRightLabel: '',
    initialLeftY: 0, initialRightY: 0,
    wrapY: true, id: 'wrap',
  });
  c.insert(0.5, 90);   // an interior point to wrap
  return c;
}

// ── 12. wrapY default flags ───────────────────────────────────────────────────
test('wrapY defaults: non-wrap curve has wrapY===false and wraps.length===0', () => {
  const c = makeStraightCurve();
  assert.equal(c.wrapY, false, 'makeStraightCurve wrapY is false');
  assert.equal(c.wraps.length, 0, 'makeStraightCurve wraps is empty');
});

test('wrapY defaults: wrapY curve has wrapY===true', () => {
  const c = makeWrapCurve();
  assert.equal(c.wrapY, true, 'makeWrapCurve wrapY is true');
});

// ── 13. displayValue wraps into [min,max) ─────────────────────────────────────
test('displayValue: maps unwrapped values into [valueMin,valueMax)', () => {
  const c = makeWrapCurve();
  // Formula: valueMin + (((v-valueMin)%p)+p)%p  where p=360
  // displayValue(-450): -180 + ((-270%360)+360)%360 = -180 + 90 = -90
  assert.ok(Math.abs(c.displayValue(-450) - (-90)) < 1e-9, 'displayValue(-450) ≈ -90');
  // displayValue(270): -180 + ((450%360)+360)%360 = -180 + 90 = -90
  assert.ok(Math.abs(c.displayValue(270)  - (-90)) < 1e-9, 'displayValue(270) ≈ -90');
  // displayValue(0): -180 + ((180%360)+360)%360 = -180 + 180 = 0
  assert.ok(Math.abs(c.displayValue(0)    -   0)   < 1e-9, 'displayValue(0) ≈ 0');
  // displayValue(180): -180 + ((360%360)+360)%360 = -180 + 0 = -180
  assert.ok(Math.abs(c.displayValue(180)  - (-180)) < 1e-9, 'displayValue(180) ≈ -180');
});

// ── 14. wrapY sample is unwrapped ─────────────────────────────────────────────
test('wrapPoint: down wrap shifts right endpoint unwrapped value by -360', () => {
  const c = makeWrapCurve();
  // Points: [0,0], [0.5,90], [1,0]. wrapPoint(1,'down') removes index 1,
  // leaving knots [0,0] and [1,0], and adds wrap {x:0.5, dir:'down'}.
  // _unwrappedKnots(): x=0 has no wrap before it → offset 0; x=1 has wrap at
  // x=0.5 before it (dir 'down' → -1) → offset = 360*(-1) = -360.
  // So unwrapped knots become [[0,0],[1,-360]].
  // At t=1 the spline returns exactly the right endpoint y=-360 (boundary case),
  // while before the wrap sample(1) = 0 (right endpoint y=0).
  // This gives a deterministic exact-360 shift at t=1.
  const before = c.sample(1);
  c.wrapPoint(1, 'down');
  const after = c.sample(1);
  assert.ok(Math.abs(after - (before - 360)) < 1e-6,
    `sample(1) after 'down' wrap ≈ ${before} - 360 = ${before - 360}, got ${after}`);
});

// ── 15. deleteWrap removes the offset ────────────────────────────────────────
test('deleteWrap: removes wrap record and restores original sample', () => {
  const c = makeWrapCurve();
  const before = c.sample(1);
  c.wrapPoint(1, 'down');
  c.deleteWrap(0);
  assert.equal(c.wraps.length, 0, 'wraps is empty after deleteWrap');
  assert.ok(Math.abs(c.sample(1) - before) < 1e-6,
    `sample(1) restored to ${before} after deleteWrap`);
});

// ── 16. toJSON/fromJSON round-trips wrapY+wraps ───────────────────────────────
test('toJSON/fromJSON: round-trips wrapY, wraps, and unwrapped sample', () => {
  const c = makeWrapCurve();
  c.wrapPoint(1, 'down');
  const c2 = Curve2D.fromJSON(c.toJSON());
  assert.equal(c2.wrapY, true, 'c2.wrapY === true after round-trip');
  assert.deepEqual(c2.wraps, c.wraps, 'c2.wraps deep-equals c.wraps');
  assert.ok(Math.abs(c2.sample(0.7) - c.sample(0.7)) < 1e-9,
    'c2.sample(0.7) matches c.sample(0.7) after round-trip');
});

// ── 17. wrapPoint/deleteWrap endpoint/range errors ───────────────────────────
test('wrapPoint: throws RangeError for left endpoint index', () => {
  const c = makeWrapCurve();
  assert.throws(() => c.wrapPoint(0, 'down'), RangeError);
});

test('wrapPoint: throws RangeError for right endpoint index', () => {
  const c = makeWrapCurve();
  assert.throws(() => c.wrapPoint(c.points.length - 1, 'up'), RangeError);
});

test('deleteWrap: throws RangeError for out-of-range wrapIndex', () => {
  const c = makeWrapCurve();
  assert.throws(() => c.deleteWrap(99), RangeError);
});

// ── 18. sampleCurve — clamp and single-waypoint ───────────────────────────────
test('sampleCurve: single waypoint returns its value for any beat', () => {
  assert.equal(sampleCurve([[5, 42]], 0), 42, 'single waypoint beat=0');
  assert.equal(sampleCurve([[5, 42]], 5), 42, 'single waypoint beat=5');
  assert.equal(sampleCurve([[5, 42]], 99), 42, 'single waypoint beat=99');
});

test('sampleCurve: clamp-below returns first value', () => {
  assert.equal(sampleCurve([[0, 0], [1, 8]], -1), 0, 'beat < first clamps to first value');
});

test('sampleCurve: clamp-above returns last value', () => {
  assert.equal(sampleCurve([[0, 0], [1, 8]], 2), 8, 'beat > last clamps to last value');
});

test('sampleCurve: clamp-at-first-beat returns first value', () => {
  assert.equal(sampleCurve([[0, 0], [1, 8]], 0), 0, 'beat == first returns first value');
});

test('sampleCurve: clamp-at-last-beat returns last value', () => {
  assert.equal(sampleCurve([[0, 0], [1, 8]], 1), 8, 'beat == last returns last value');
});

// ── 19. sampleCurve — easing formulas at t=0.5 ───────────────────────────────
// Setup: v0=0, v1=8, segment [0,1], beat=0.5 → t=0.5
// linear:     t=0.5          → 0 + 0.5*8       = 4.0
// ease-in:    t²=0.25        → 0 + 0.25*8      = 2.0
// ease-out:   t(2-t)=0.75    → 0 + 0.75*8      = 6.0
// anticipate: t²((s+1)t-s)=0.25*(3*0.5-2)=0.25*(-0.5)=-0.125 → 0+(-0.125)*8 = -1.0
// spring:     1-anticipate(0.5)=1-(-0.125)=1.125 → 0+1.125*8 = 9.0
const EASE_WAYPOINTS_V0 = 0, EASE_WAYPOINTS_V1 = 8;
const makeEaseWaypoints = (easeName) =>
  [[0, EASE_WAYPOINTS_V0, easeName], [1, EASE_WAYPOINTS_V1]];

test('sampleCurve: linear at t=0.5 yields 4.0', () => {
  assert.equal(sampleCurve(makeEaseWaypoints('linear'), 0.5), 4.0);
});

test('sampleCurve: ease-in at t=0.5 yields 2.0', () => {
  assert.equal(sampleCurve(makeEaseWaypoints('ease-in'), 0.5), 2.0);
});

test('sampleCurve: ease-out at t=0.5 yields 6.0', () => {
  assert.equal(sampleCurve(makeEaseWaypoints('ease-out'), 0.5), 6.0);
});

test('sampleCurve: anticipate at t=0.5 yields -1.0', () => {
  const result = sampleCurve(makeEaseWaypoints('anticipate'), 0.5);
  assert.ok(Math.abs(result - (-1.0)) < 1e-12,
    `anticipate at t=0.5: expected -1.0, got ${result}`);
});

test('sampleCurve: spring at t=0.5 yields 9.0', () => {
  const result = sampleCurve(makeEaseWaypoints('spring'), 0.5);
  assert.ok(Math.abs(result - 9.0) < 1e-12,
    `spring at t=0.5: expected 9.0, got ${result}`);
});

// ── 20. sampleCurve — unknown easing throws Error ────────────────────────────
test('sampleCurve: unknown easing name throws Error', () => {
  assert.throws(
    () => sampleCurve([[0, 0, 'bogus'], [1, 8]], 0.5),
    Error,
  );
});

// ── 21. applyEasing — unknown name throws Error ───────────────────────────────
test('applyEasing: unknown easing name throws Error', () => {
  assert.throws(() => applyEasing('nope', 0.5), Error);
});
