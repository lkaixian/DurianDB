"""Operator notes on a point. Run: python tests/test_notes.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian import notes  # noqa: E402
from durian.app import ApiError, App  # noqa: E402

store = new_store()
app = App(store)
app.auth.bootstrap("admin-password-1")
admin = app.auth.identity(app.login({"username": "admin", "password": "admin-password-1"}))
admin_id = {"name": "admin", "roles": {"superadmin", "user"}}
anon = {"name": None, "roles": {"anonymous"}}

call = lambda ident, m, p, b=None: app.handle(m, p, {}, b, ident)

# a point to hang them on
assert call(admin, "PUT", "/api/config/source/v", {"type": "virtual"})[0] == 200
assert call(admin, "PUT", "/api/config/point/temp", {"source": "v", "generator": {"kind": "constant", "value": 1}})[0] == 200

# nobody anonymous writes one, and an empty or overlong note is refused with the field named
try:  # a point an anonymous caller cannot see is not revealed to exist, signed in or not
    call(anon, "POST", "/api/notes/temp", {"text": "hi"})
    raise AssertionError("anonymous wrote a note")
except ApiError as ex:
    assert ex.status == 404
try:
    call(admin, "POST", "/api/notes/temp", {"text": "  "})
    raise AssertionError("blank note accepted")
except ApiError as ex:
    assert ex.status == 422 and ex.errors[0]["property"] == "text"
try:
    call(admin, "POST", "/api/notes/temp", {"text": "x" * 2001})
    raise AssertionError("overlong note accepted")
except ApiError as ex:
    assert ex.status == 422 and ex.errors[0]["property"] == "text"

# a note is stored with who wrote it, and comes back oldest first
first = call(admin, "POST", "/api/notes/temp", {"text": "  pump 2 tripping on start  "})[1]
assert first["text"] == "pump 2 tripping on start" and first["user"] == "admin"
call(admin, "POST", "/api/notes/temp", {"text": "electrician due at 14:00"})
got = call(admin, "GET", "/api/notes/temp", None)[1]
assert [n["text"] for n in got] == ["pump 2 tripping on start", "electrician due at 14:00"]

# notes stay with their own point
try:
    call(admin, "GET", "/api/notes/nope", None)
    raise AssertionError("notes for a point that does not exist")
except ApiError as ex:
    assert ex.status == 404

# the oldest are dropped past the cap, so a noisy shift cannot grow the table without bound
for i in range(notes.MAX_PER_POINT + 5):
    notes.add(store, "temp", f"note {i}", "admin")
kept = notes.for_point(store, "temp")
assert len(kept) == notes.MAX_PER_POINT and kept[-1]["text"] == f"note {notes.MAX_PER_POINT + 4}"

# it was audited, so a handover can be traced
assert "note.add" in [a["action"] for a in store.q("select action from audit where user='admin'")]

print("ok")
