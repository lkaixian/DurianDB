"""Points as a spreadsheet: one row per point, the common settings in their own columns, everything else as JSON in `extra`.

Export then import round-trips. Import checks every row with the normal point validation before anything is written.
"""
import csv
import io
import json

COLUMNS = ["name", "source", "unit", "description", "tags", "logging", "tolerance", "settable", "read_roles", "set_roles",
           "eu_low", "eu_high", "decimals", "set_min", "set_max", "max_step", "confirm", "extra"]
LISTS = ("read_roles", "set_roles")
NUMBERS = ("tolerance", "eu_low", "eu_high", "decimals", "set_min", "set_max", "max_step")
BOOLS = ("settable", "confirm")
MAX_ROWS = 5000


def export(points):
    """CSV bytes for a list of point config bodies (each with its `name`)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for p in points:
        extra = {k: v for k, v in p.items() if k not in COLUMNS}
        row = []
        for c in COLUMNS:
            v = p.get(c)
            if c == "extra":
                v = json.dumps(extra, separators=(",", ":")) if extra else ""
            elif c == "tags":
                v = ";".join(f"{k}:{x}" for k, x in (v or {}).items())
            elif c in LISTS:
                v = ";".join(v or [])
            elif v is None:
                v = ""
            elif c in BOOLS:
                v = "true" if v else "false"
            row.append(v)
        w.writerow(row)
    return out.getvalue().encode()


def parse(text):
    """([(name, body)], [{line, point, property, message}]). Reads only; nothing is validated against the store here."""
    rows, errors = [], []

    def bad(line, name, prop, msg):
        errors.append({"line": line, "point": name, "property": prop, "message": msg})

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "name" not in reader.fieldnames or "source" not in reader.fieldnames:
        return [], [{"line": 1, "point": None, "property": "header", "message": "the first line must name the columns, at least name and source"}]
    unknown = [c for c in reader.fieldnames if c not in COLUMNS]
    if unknown:
        return [], [{"line": 1, "point": None, "property": "header", "message": f"unknown column(s): {', '.join(unknown)}"}]
    seen = set()
    for n, r in enumerate(reader, 2):
        if n - 1 > MAX_ROWS:
            bad(n, None, "file", f"more than {MAX_ROWS} rows")
            break
        name = (r.get("name") or "").strip()
        if name in seen:
            bad(n, name, "name", "listed twice in this file")
        seen.add(name)
        body, ok = {}, True
        for c in COLUMNS[1:]:
            v = (r.get(c) or "").strip()
            if v == "":
                continue
            try:
                if c == "extra":
                    x = json.loads(v)
                    if not isinstance(x, dict):
                        raise ValueError("must be a JSON object")
                    body.update({k: y for k, y in x.items() if k not in body})
                elif c == "tags":
                    body[c] = dict(t.split(":", 1) for t in v.split(";") if t.strip())
                elif c in LISTS:
                    body[c] = [t.strip() for t in v.split(";") if t.strip()]
                elif c in NUMBERS:
                    body[c] = int(v) if v.lstrip("-").isdigit() else float(v)
                elif c in BOOLS:
                    if v.lower() not in ("true", "false"):
                        raise ValueError("write true or false")
                    body[c] = v.lower() == "true"
                else:
                    body[c] = v
            except (ValueError, TypeError) as e:
                bad(n, name, c, str(e) if isinstance(e, ValueError) and not str(e).startswith(("could not", "invalid literal", "not enough")) else "not a valid value")
                ok = False
        if ok:
            rows.append((name, body))
    return rows, errors
