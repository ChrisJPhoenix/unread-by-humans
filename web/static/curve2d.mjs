'use strict';

// ============================================================
// Spline math — Fritsch-Carlson monotone cubic Hermite
// Spec: design/2d-slider.md §Spline math
// ============================================================

// Split a sorted points array into runs at adjacency boundaries.
// Two consecutive points are "adjacent" (vertical step) when their
// edit-pixel x differs by exactly 1. editorW is the inner CSS-px width.
function splitAtAdjacencies(points, editorW) {
  const runs = [];
  let runStart = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const pxA = Math.round(points[i][0]   * (editorW - 1));
    const pxB = Math.round(points[i+1][0] * (editorW - 1));
    if (pxB - pxA === 1) {
      runs.push(points.slice(runStart, i + 1));
      runStart = i + 1;
    }
  }
  runs.push(points.slice(runStart));
  return runs.filter(r => r.length > 0);
}

// Run Fritsch-Carlson on one adjacency-free run and append segments to out.
function appendFritschCarlsonSegments(pts, out) {
  const n = pts.length;
  if (n < 2) return;

  const delta = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = pts[i+1][0] - pts[i][0];
    delta.push(dx === 0 ? 0 : (pts[i+1][1] - pts[i][1]) / dx);
  }

  const m = new Array(n);
  m[0]   = delta[0];
  m[n-1] = delta[n-2];
  for (let i = 1; i < n - 1; i++) m[i] = (delta[i-1] + delta[i]) / 2;

  for (let i = 0; i < n - 1; i++) {
    if (delta[i] === 0) {
      m[i] = 0; m[i+1] = 0;
    } else {
      const alpha = m[i]   / delta[i];
      const beta  = m[i+1] / delta[i];
      const sq = alpha * alpha + beta * beta;
      if (sq > 9) {
        const k = 3 / Math.sqrt(sq);
        m[i] *= k; m[i+1] *= k;
      }
    }
  }

  for (let i = 0; i < n - 1; i++) {
    out.push({ x0: pts[i][0], y0: pts[i][1], x1: pts[i+1][0], y1: pts[i+1][1],
               m0: m[i], m1: m[i+1] });
  }
}

// Build the full segment array, splitting at adjacencies.
// editorW: inner CSS-px width of the editing context (for adjacency threshold).
// Pass a large number (e.g. 10000) when no editor context exists; adjacency
// detection is a no-op unless points were snapped at edit-pixel resolution.
export function buildSegments(points, editorW) {
  const W = editorW || 10000;
  const out = [];
  for (const run of splitAtAdjacencies(points, W)) {
    appendFritschCarlsonSegments(run, out);
  }
  return out;
}

// Evaluate the piecewise Hermite spline at parameter t ∈ [0,1].
export function evaluate(segments, t) {
  if (segments.length === 0) return 0;
  if (t <= segments[0].x0) return segments[0].y0;
  if (t >= segments[segments.length - 1].x1) return segments[segments.length - 1].y1;

  let lo = 0, hi = segments.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].x1 < t) lo = mid + 1; else hi = mid;
  }
  const s = segments[lo];
  const h = s.x1 - s.x0;
  if (h === 0) return s.y1;
  const u = (t - s.x0) / h, u2 = u*u, u3 = u2*u;
  return (2*u3 - 3*u2 + 1) * s.y0
       + (u3 - 2*u2 + u)   * h * s.m0
       + (-2*u3 + 3*u2)    * s.y1
       + (u3 - u2)         * h * s.m1;
}

// ============================================================
// Curve2D — pure model, zero DOM access
// Spec: design/js-2d-slider.md §Curve2D model
// ============================================================
export class Curve2D {
  constructor({ valueMin, valueMax, valueMinLabel, valueMaxLabel,
                timeLeftLabel, timeRightLabel, initialLeftY, initialRightY, id, wrapY }) {
    this.valueMin       = valueMin;
    this.valueMax       = valueMax;
    this.valueMinLabel  = valueMinLabel;
    this.valueMaxLabel  = valueMaxLabel;
    this.timeLeftLabel  = timeLeftLabel;
    this.timeRightLabel = timeRightLabel;
    this.initialLeftY   = initialLeftY;
    this.initialRightY  = initialRightY;
    this.id             = id;
    this.wrapY          = wrapY || false;
    this.points         = [[0, initialLeftY], [1, initialRightY]];
    this.wraps          = [];
    this._observers     = [];
  }

  // Subscribe fn(changeRecord). Returns unsubscribe().
  onChange(fn) {
    this._observers.push(fn);
    return () => { this._observers = this._observers.filter(f => f !== fn); };
  }

  // Emit a change record to all observers.
  _emit(record) {
    for (const fn of this._observers) fn(record);
  }

  _pointsEqual(a, b) { return a[0] === b[0] && a[1] === b[1]; }

  // Insert an interior point (x must be in (0,1), y in [valueMin,valueMax]).
  // Collision threshold: 1/9999 in storage units (fine-grained guard for the
  // model-only API; the editor uses insertWithEditorWidth for pixel-accurate checks).
  insert(x, y, { source } = {}) {
    if (x <= 0 || x >= 1)
      throw new RangeError(`insert: x=${x} must be strictly in (0,1)`);
    if (y < this.valueMin || y > this.valueMax)
      throw new RangeError(`insert: y=${y} out of [${this.valueMin},${this.valueMax}]`);
    for (const p of this.points) {
      if (Math.abs(p[0] - x) < 1/9999)
        throw new RangeError(`insert: x=${x} collides with existing point`);
    }
    const old = this._snapshotPoints();
    this.points = [...this.points, [x, y]].sort((a, b) => a[0] - b[0]);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  // Insert using edit-pixel collision detection; returns the new point's index.
  insertWithEditorWidth(x, y, editorW, { source } = {}) {
    if (x <= 0 || x >= 1)
      throw new RangeError(`insert: x=${x} must be strictly in (0,1)`);
    if (y < this.valueMin || y > this.valueMax)
      throw new RangeError(`insert: y=${y} out of [${this.valueMin},${this.valueMax}]`);
    const xPx = Math.round(x * (editorW - 1));
    for (const p of this.points) {
      if (Math.abs(Math.round(p[0] * (editorW - 1)) - xPx) < 1)
        throw new RangeError(`insert: x=${x} collides with existing point`);
    }
    const old = this._snapshotPoints();
    this.points = [...this.points, [x, y]].sort((a, b) => a[0] - b[0]);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
    return this.points.findIndex(p => p[0] === x);
  }

  // Remove the i-th point; refuses endpoints.
  remove(i, { source } = {}) {
    if (i === 0 || i === this.points.length - 1)
      throw new RangeError(`remove: cannot remove endpoint at index ${i}`);
    const old = this._snapshotPoints();
    this.points = this.points.filter((_, idx) => idx !== i);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  // Reposition the i-th point, with equality gate and y-linking for endpoints.
  move(i, x, y, { source } = {}) {
    const isLeft     = i === 0;
    const isRight    = i === this.points.length - 1;
    const isEndpoint = isLeft || isRight;
    const clampedX   = isLeft ? 0 : isRight ? 1 : x;
    const clampedY   = Math.max(this.valueMin, Math.min(this.valueMax, y));
    if (this._pointsEqual(this.points[i], [clampedX, clampedY])) return; // equality gate
    const old        = this._snapshotPoints();
    this.points[i]   = [clampedX, clampedY];
    if (isEndpoint) {
      const other = isLeft ? this.points.length - 1 : 0;
      this.points[other] = [this.points[other][0], clampedY];
    }
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  // Restore to the as-constructed 2-point state.
  reset({ source } = {}) {
    const initial = [[0, this.initialLeftY], [1, this.initialRightY]];
    if (this.points.length === 2 &&
        this._pointsEqual(this.points[0], initial[0]) &&
        this._pointsEqual(this.points[1], initial[1])) return; // equality gate
    const old = this._snapshotPoints();
    this.points = initial.map(p => [...p]);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  // Private helpers for toroidal wrap.
  _period()             { return this.valueMax - this.valueMin; }
  _wrapOffsetAt(x)      {
    let count = 0;
    for (const w of this.wraps) {
      if (w.x < x) count += (w.dir === 'up' ? 1 : -1);
    }
    return this._period() * count;
  }
  _unwrappedKnots()     { return this.points.map(([x, y]) => [x, y + this._wrapOffsetAt(x)]); }
  _segmentKnots()       { return this.wrapY ? this._unwrappedKnots() : this.points; }

  // Map any unwrapped (multi-turn) value into [valueMin, valueMax).
  displayValue(v) {
    const p = this._period();
    return this.valueMin + (((v - this.valueMin) % p) + p) % p;
  }

  // Pure reads — fire nothing.
  sample(t)        { return evaluate(buildSegments(this._segmentKnots()), t); }
  sampleMany(ts)   { const s = buildSegments(this._segmentKnots()); return Array.from(ts, t => evaluate(s, t)); }

  // Convert interior control point i into a wrap breadcrumb.
  wrapPoint(i, dir, { source } = {}) {
    if (i === 0 || i === this.points.length - 1)
      throw new RangeError(`wrapPoint: cannot wrap endpoint at index ${i}`);
    const old    = this._snapshotPoints();
    const oldX   = this.points[i][0];
    this.points  = this.points.filter((_, idx) => idx !== i);
    this.wraps.push({ x: oldX, dir });
    this.wraps.sort((a, b) => a.x - b.x);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  // Remove the wrap record at wrapIndex.
  deleteWrap(wrapIndex, { source } = {}) {
    if (wrapIndex < 0 || wrapIndex >= this.wraps.length)
      throw new RangeError(`deleteWrap: wrapIndex=${wrapIndex} out of range`);
    const old = this._snapshotPoints();
    this.wraps.splice(wrapIndex, 1);
    this._emit({ field: 'points', oldValue: old, newValue: this._snapshotPoints(), source, curveId: this.id });
  }

  toJSON() {
    return {
      id: this.id, valueMin: this.valueMin, valueMax: this.valueMax,
      valueMinLabel: this.valueMinLabel, valueMaxLabel: this.valueMaxLabel,
      timeLeftLabel: this.timeLeftLabel, timeRightLabel: this.timeRightLabel,
      initialLeftY: this.initialLeftY, initialRightY: this.initialRightY,
      points: this._snapshotPoints(),
      wrapY: this.wrapY,
      wraps: this.wraps.map(w => ({ x: w.x, dir: w.dir })),
    };
  }

  static fromJSON(o) {
    const curve = new Curve2D(o);
    const pts = o.points;
    // Restore endpoint y values from the saved data.
    curve.points[0][1] = pts[0][1];
    curve.points[1][1] = pts[pts.length - 1][1];
    // Insert any interior points (skip index 0 and last).
    for (let i = 1; i < pts.length - 1; i++) curve.insert(pts[i][0], pts[i][1]);
    curve.wrapY = o.wrapY || false;
    curve.wraps = (o.wraps || []).map(w => ({ x: w.x, dir: w.dir }));
    return curve;
  }

  _snapshotPoints() { return this.points.map(p => [...p]); }
}

// ============================================================
// Waypoint sampler — named-easing beat interpolation
// Mirrors frame_driver._interp_between_waypoints / _ease exactly.
// Spec: design/create-render.md §Waypoint easing
// ============================================================

const _BACK_OVERSHOOT = 2.0;

/** Apply a named easing to normalized segment progress t ∈ [0,1].
 * @param {string} name  One of: linear, ease-in, ease-out, anticipate, spring
 * @param {number} t     Progress in [0,1]
 * @returns {number}
 * @throws {Error} on unknown easing name
 */
export function applyEasing(name, t) {
  switch (name) {
    case 'linear':     return t;
    case 'ease-in':    return t * t;
    case 'ease-out':   return t * (2.0 - t);
    case 'anticipate': return t * t * ((_BACK_OVERSHOOT + 1.0) * t - _BACK_OVERSHOOT);
    case 'spring':     {
      const t1 = 1.0 - t;
      const backIn = t1 * t1 * ((_BACK_OVERSHOOT + 1.0) * t1 - _BACK_OVERSHOOT);
      return 1.0 - backIn;
    }
    default: throw new Error(`unknown easing: ${JSON.stringify(name)}`);
  }
}

/** Interpolate a query beat through an ordered waypoint list with per-segment
 * named easings, mirroring frame_driver._interp_between_waypoints exactly.
 *
 * Each waypoint is [beat, value] or [beat, value, easingName].
 * The easing is the optional 3rd element of the SEGMENT-START waypoint
 * (default "linear").  Clamps below-range to the first value and above-range
 * to the last value.  A single waypoint returns its value for all beats.
 *
 * @param {Array<[number, number, string?]>} waypoints  Ordered [beat,value,ease?] list
 * @param {number} beat  Query beat position
 * @returns {number}
 * @throws {Error} on unknown easing name
 */
export function sampleCurve(waypoints, beat) {
  if (waypoints.length === 1) return waypoints[0][1];

  if (beat <= waypoints[0][0]) return waypoints[0][1];
  if (beat >= waypoints[waypoints.length - 1][0]) return waypoints[waypoints.length - 1][1];

  for (let i = 0; i < waypoints.length - 1; i++) {
    const start = waypoints[i];
    const end   = waypoints[i + 1];
    const b0 = start[0], v0 = start[1];
    const b1 = end[0],   v1 = end[1];
    if (b0 <= beat && beat <= b1) {
      const t = (b1 !== b0) ? (beat - b0) / (b1 - b0) : 0.0;
      const easingName = start.length > 2 ? start[2] : 'linear';
      return v0 + applyEasing(easingName, t) * (v1 - v0);
    }
  }

  throw new Error('sampleCurve: no segment matched (unreachable)');
}
