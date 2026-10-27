"""Engineering unit conversion for display.

Stored values are always in the point's own `unit`; conversion happens only on the way out to a person.
A point may carry a `display_unit` in the same dimension, and the API exposes the converted `display_value`.
Each unit names its dimension and the affine map to that dimension's base: base = value * scale + offset.
"""
import math

# unit -> (dimension, scale, offset), so value in `unit` maps to `value * scale + offset` in the base unit.
# temperature: K, pressure: Pa, flow: L/s, length: m, mass: kg, ratio: 0-1
TABLE = {
    "C": ("temperature", 1.0, 273.15),
    "F": ("temperature", 5.0 / 9.0, 255.3722222222222),  # (F - 32) * 5/9 + 273.15
    "K": ("temperature", 1.0, 0.0),

    "Pa": ("pressure", 1.0, 0.0),
    "kPa": ("pressure", 1000.0, 0.0),
    "bar": ("pressure", 100000.0, 0.0),
    "psi": ("pressure", 6894.757293168, 0.0),

    "L/s": ("flow", 1.0, 0.0),
    "L/min": ("flow", 1.0 / 60.0, 0.0),
    "m3/h": ("flow", 1000.0 / 3600.0, 0.0),
    "gpm": ("flow", 3.785411784 / 60.0, 0.0),  # US gallon per minute

    "m": ("length", 1.0, 0.0),
    "mm": ("length", 0.001, 0.0),
    "ft": ("length", 0.3048, 0.0),
    "in": ("length", 0.0254, 0.0),

    "kg": ("mass", 1.0, 0.0),
    "g": ("mass", 0.001, 0.0),
    "lb": ("mass", 0.45359237, 0.0),

    "%": ("ratio", 0.01, 0.0),
    "ppm": ("ratio", 0.000001, 0.0),
    "0-1": ("ratio", 1.0, 0.0),
}


def _unit(unit):
    """The table row for a unit, or a sentence naming what went wrong."""
    try:
        row = TABLE.get(unit)
    except TypeError:  # a list or another unhashable thing was given where a unit name belongs
        row = None
    if row is None:
        raise ValueError(f"unknown unit {unit!r}; known units are {', '.join(TABLE)}")
    return row


def convert(value, frm, to):
    """Convert `value` from unit `frm` to unit `to`. Raises ValueError with a sentence for an unknown unit or a
    conversion between different kinds of quantity."""
    a, b = _unit(frm), _unit(to)
    if a[0] != b[0]:
        raise ValueError(f"cannot convert {frm} to {to}: different kinds of quantity")
    return (value * a[1] + a[2] - b[2]) / b[1]


def compatible(frm, to):
    """True when both units are known and measure the same quantity: the check a display unit must pass."""
    try:
        a, b = TABLE.get(frm), TABLE.get(to)
    except TypeError:
        return False
    return a is not None and b is not None and a[0] == b[0]


def format(value, unit="", with_unit=False):
    """A value rounded for display: fewer decimals as it grows. The unit is appended only when `with_unit` asks."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        text = "" if value is None else str(value)
    else:
        if not math.isfinite(v):
            text = str(v)
        else:
            a = abs(v)
            digits = 0 if a >= 1000 else 1 if a >= 100 else 2 if a >= 1 else 3 if a >= 0.01 else 6
            text = f"{v:.{digits}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}" if with_unit and unit else text
