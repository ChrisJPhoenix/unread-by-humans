# slider_2d

A reusable widget for specifying a scalar value as a function of time — a
1D curve drawn and edited on a 2D canvas.  Used for per-instrument volume
automation (`music`) and animated effect parameters (`movie`).

## Public API

```python
from libs.slider_2d import (
    Curve,          # data model
    sample,         # sample curve at t ∈ [0, 1]  (monotone Hermite spline)
    sample_many,    # vectorised sample over np.ndarray of t values
    to_dict,        # serialise to JSON-safe dict
    from_dict,      # deserialise from dict
    outgoing_value, # compute int CC/bend value from float sample
    automation_phase,# normalised phase within a repeating cycle
    send_key,       # hashable MIDI destination key for dedup tracking
    should_send,    # True when value changed since last send
)
```

`sample` evaluates the monotone Hermite spline via
`evaluate(build_segments(curve.points), t)` (from `spline.py`).

The widget half (`ThumbnailWidget`, `EditPopup`, and the pure mouse-event
handlers in `handlers.py`) is not included in this repository because it is
tkinter-bound.

## Files

| File | Role |
|---|---|
| `curve.py` | `Curve` dataclass, `insert`, `remove`, `move`, `reset`, `sample`, `to_dict`, `from_dict`, `sample_many` |
| `spline.py` | Fritsch–Carlson monotone cubic Hermite math: `build_segments`, `evaluate` |
| `automation_send.py` | pure outgoing-value/dedupe helpers for real-time MIDI automation sends |
| `__init__.py` | Re-exports the public API |
| `test_primary.txt` | `--quick` plan: model-layer unit tests |
| `test_automation_send.txt` | `--full` plan: automation send helper unit tests |

## Coordinate systems

Three spaces; keep them straight.

| Space | x range | y range |
|---|---|---|
| **Storage** | `[0, 1]` float | `[value_min, value_max]` float |
| **Edit-display** | `[0, inner_w_px - 1]` int | `[0, inner_h_px - 1]` int |
| **Thumbnail** | same as edit-display at thumb size | same |

`x_px = round(x * (W - 1))`, `x = x_px / (W - 1)`.

## Dependencies

Standard library only for `curve.py` and `spline.py`.
`curve.py` and `sample_many` require `numpy`.
