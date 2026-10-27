"""Operator notes: what a shift wrote about a point, kept with the point rather than in a logbook.

Stored in the objects table as kind "note" and name "<point>/<id>", so no migration is needed. A note is written
by a signed-in person, is visible to anyone who may read the point, and is never edited: a later shift adds its own.
"""
import secrets
import time

KIND = "note"
MAX_TEXT = 2000
MAX_PER_POINT = 200  # oldest are dropped beyond this: notes are a handover, not an archive


def validate(body):
    t = (body or {}).get("text")
    if not isinstance(t, str) or not t.strip():
        return [{"property": "text", "message": "write something"}]
    if len(t) > MAX_TEXT:
        return [{"property": "text", "message": f"at most {MAX_TEXT} characters"}]
    return []


def add(store, point, text, user):
    """Returns the stored note. Drops the oldest notes of that point past the cap."""
    note = {"point": point, "text": text.strip(), "user": user, "ts": time.time(),
            "id": f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"}
    store.put(KIND, f"{point}/{note['id']}", {k: v for k, v in note.items() if k not in ("name", "point")})
    old = for_point(store, point)
    for n in old[:-MAX_PER_POINT]:
        del_note(store, point, n["id"])
    return note


def for_point(store, point):
    """Oldest first, so the console reads like a logbook."""
    notes = [{"point": point, **n} for n in store.list(KIND) if str(n.get("name", "")).startswith(point + "/")]
    # ts alone is not an order two notes written in the same instant agree on, and the table returns rows in
    # whatever order it likes: the id breaks the tie so the logbook reads the same everywhere.
    return sorted(notes, key=lambda n: (n.get("ts", 0), str(n.get("id", ""))))


def del_note(store, point, note_id):
    return store.delete(KIND, f"{point}/{note_id}")
