"""Minimal Excel (.xlsx) and PDF writers for tabular downloads, stdlib only.

Both take the same header and rows as csv_bytes. Cells: numbers stay numbers, None is empty, everything else is text
(never a formula: xlsx text is stored inline, so a value like =1+1 is just that text).
"""
import io
import math
import re
import zipfile
from xml.sax.saxutils import escape

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _col(i):  # 0 -> A, 26 -> AA
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, v, style=""):
    if v is None:
        return ""
    if _is_num(v):
        return f'<c r="{ref}"{style}><v>{v!r}</v></c>'
    return f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(_CTRL.sub("", str(v)))}</t></is></c>'


def xlsx_bytes(header, rows):
    """A one-sheet workbook with a bold header row."""
    lines = []
    for r, row in enumerate([header, *rows], 1):
        style = ' s="1"' if r == 1 else ""
        lines.append(f'<row r="{r}">' + "".join(_cell(f"{_col(c)}{r}", v, style) for c, v in enumerate(row)) + "</row>")
    parts = {
        "[Content_Types].xml": _HDR + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{_CT}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_CT}.worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{_CT}.styles+xml"/></Types>',
        "_rels/.rels": _HDR + f'<Relationships xmlns="{_PKG}"><Relationship Id="rId1" '
        f'Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": _HDR + f'<workbook {_NS} xmlns:r="{_REL}">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": _HDR + f'<Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{_REL}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": _HDR + f'<styleSheet {_NS}><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": _HDR + f"<worksheet {_NS}><sheetData>" + "".join(lines) + "</sheetData></worksheet>",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buf.getvalue()


def _pdf_text(v, width):
    """Latin-1 bytes, truncated to width characters, with ( ) \\ escaped; anything outside latin-1 becomes ?."""
    s = "" if v is None else (repr(v) if _is_num(v) else str(v))
    s = " ".join(s.split())
    if len(s) > width:
        s = s[:max(width - 2, 0)] + ".."
    b = s.encode("latin-1", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def pdf_bytes(title, header, rows, per_page=44):
    """A landscape A4 PDF 1.4 table in Helvetica 8pt, header repeated on every page, long cells truncated."""
    w, h, margin, lead = 842, 595, 40, 11
    cols = max(len(header), 1)
    colw = (w - 2 * margin) / cols
    chars = int(colw / 4.4)  # Helvetica averages about 0.55 em at 8pt
    rows = list(rows)
    chunks = [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    bold = 4 + 2 * len(chunks)
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            bold: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"}
    kids = []
    for n, chunk in enumerate(chunks):
        pno, cno = 4 + 2 * n, 5 + 2 * n
        ops = [b"BT /F2 12 Tf %d %d Td (" % (margin, h - margin) + _pdf_text(title, 90) + b") Tj ET",
               b"BT /F1 7 Tf %d %d Td (Page %d of %d) Tj ET" % (w - margin - 60, h - margin, n + 1, len(chunks))]
        y = h - margin - 2 * lead
        for r, row in enumerate([header, *chunk]):
            font = b"/F2" if r == 0 else b"/F1"
            for c, v in enumerate(list(row)[:cols]):
                ops.append(b"BT %s 8 Tf %.1f %d Td (" % (font, margin + c * colw, y) + _pdf_text(v, chars) + b") Tj ET")
            y -= lead
        stream = b"\n".join(ops)
        objs[cno] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objs[pno] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                     b"/Resources << /Font << /F1 3 0 R /F2 %d 0 R >> >> >>" % (w, h, cno, bold))
        kids.append(b"%d 0 R" % pno)
    objs[2] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num in sorted(objs):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
