"""How every CSV download is formatted: UTC ISO-8601 timestamps and a plain newline-terminated CSV.

The history and bundle exports keep milliseconds; the journal and report use whole seconds. Sharing these
means the five export paths cannot drift apart byte by byte.
"""
import csv
import datetime as dt
import io


def iso(ts):
    """UTC ISO-8601 to the whole second. A missing time is an empty cell, not the word None."""
    if ts is None:
        return ""
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):  # outside 1970..9999 or not finite: an empty cell
        return ""


def iso_millis(ts):
    """UTC ISO-8601 to the millisecond, for history rows whose samples can share a second."""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def csv_bytes(header, rows):
    """The header and rows as UTF-8 CSV bytes, with '\\n' between lines on every platform."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode()
