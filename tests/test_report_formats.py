"""Report as Excel and PDF: valid containers, right cells, safe text, correct xref, pagination. Run: python tests/test_report_formats.py"""
import io
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import docfmt  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEAD = ["point", "n", "note"]
ROWS = [["a&<b>", 1, "=cmd()"], ["+x", 2.5, None], ["@y", -3, "-1+1"]]

# ---- xlsx
z = zipfile.ZipFile(io.BytesIO(docfmt.xlsx_bytes(HEAD, ROWS)))
for n in z.namelist():
    ET.fromstring(z.read(n))  # every part is well-formed XML
assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml"} <= set(z.namelist())
rows = ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(f".//{NS}row")
assert len(rows) == 4
got = []
for r in rows:
    got.append([(c.get("t"), "".join(c.itertext())) for c in r.findall(f"{NS}c")])
assert got[0] == [("inlineStr", "point"), ("inlineStr", "n"), ("inlineStr", "note")], got[0]
assert got[1] == [("inlineStr", "a&<b>"), (None, "1"), ("inlineStr", "=cmd()")], got[1]
assert got[2][0] == ("inlineStr", "+x") and got[2][1] == (None, "2.5"), got[2]   # None cell is simply absent
assert got[3] == [("inlineStr", "@y"), (None, "-3"), ("inlineStr", "-1+1")]
assert b"<f>" not in z.read("xl/worksheets/sheet1.xml")  # no formula element anywhere

# ---- pdf
def check_pdf(b, pages):
    assert b.startswith(b"%PDF-") and b.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", b).group(1))
    assert b[start:start + 4] == b"xref"
    objs = [int(m.group(1)) for m in re.finditer(rb"(?m)^(\d+) 0 obj", b)]
    offs = re.findall(rb"(\d{10}) 00000 n ", b)
    assert len(offs) == len(objs), (len(offs), len(objs))
    for i, o in enumerate(offs, 1):
        assert b[int(o):].startswith(b"%d 0 obj" % i), (i, o)
    assert len(re.findall(rb"/Type /Page\b", b)) == pages, pages

check_pdf(pdf := docfmt.pdf_bytes(r"T (x) \ é€", HEAD, ROWS), 1)
assert rb"\(x\)" in pdf and rb"\\" in pdf
big = docfmt.pdf_bytes("Big", HEAD, [[f"p{i}", i, "z" * 500] for i in range(200)])
n = len(re.findall(rb"/Type /Page\b", big))
assert n > 1
check_pdf(big, n)
assert b"z" * 500 not in big  # long cells truncated

# ---- through the API
app = App(new_store(), Path(tempfile.mkdtemp()))
seed(app.store)
app.auth.bootstrap("admin-password-123")
who = app.auth.identity(app.auth.login("admin", "admin-password-123"))
for fmt, ctype, name, magic in (("xlsx", "spreadsheetml.sheet", "report.xlsx", b"PK"), ("pdf", "application/pdf", "report.pdf", b"%PDF-"), ("csv", "text/csv", "report.csv", b"point")):
    s, raw = app.handle("GET", "/api/reports/summary", {"format": fmt}, None, who)
    assert s == 200 and ctype in raw.ctype and raw.filename == name and raw.data.startswith(magic), (fmt, raw.ctype)
assert isinstance(app.handle("GET", "/api/reports/summary", {"format": "zzz"}, None, who)[1], dict)
# ---- edge cases: absurd window bounds are a 400, a lone surrogate is stored text not a crash
for q in ({"to": "1e12", "from": "999999996400"}, {"to": "nan"}, {"from": "-1e300", "to": "1"}, {"to": "inf"}):
    for fmt in ("xlsx", "pdf", "csv"):
        try:
            app.handle("GET", "/api/reports/summary", {"format": fmt, **q}, None, who)
            raise AssertionError((fmt, q))
        except ApiError as e:
            assert e.status == 400 and "numbers" in str(e), (fmt, q, e)
SUR = "a" + chr(0xD800) + "b"
ET.fromstring(zipfile.ZipFile(io.BytesIO(docfmt.xlsx_bytes(HEAD, [[SUR, 1, "u"]]))).read("xl/worksheets/sheet1.xml"))
check_pdf(docfmt.pdf_bytes("t" + chr(0xD800), HEAD, [[SUR, 1, "u"]]), 1)
from durian.csvfmt import iso  # noqa: E402
assert isinstance(iso(1e12), str) and isinstance(iso(float("nan")), str) and isinstance(iso(1e300), str)
print("ok")
