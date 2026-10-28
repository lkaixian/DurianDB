"""On-disk overflow for history the database would not take, so a disk-full, locked or stopped database loses nothing.

NDJSON, one reading per line, appended a batch at a time with one fsync per batch. The file rolls over into numbered
segments (`spool.ndjson.1`, `.2` ... oldest first, the live one last); when the total passes `max_bytes` the oldest
segment is dropped and its rows counted. Replay hands rows to `add_history` (insert-or-ignore on point and time), and a
segment is deleted only after all of it is stored, so a crash mid-replay re-sends rows the database ignores: never a
loss, never a double count.
"""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("durian.spool")


class Spool:
    def __init__(self, path, max_bytes=50_000_000):
        self.path, self.max_bytes, self.dropped, self.lock = Path(path), max_bytes, 0, threading.Lock()
        self.counts = {}  # segment file -> readings in it
        for f in self._files():
            n = 0
            for _ in self._read(f):
                n += 1
            self.counts[f] = n
            if f.stat().st_size and not f.read_bytes().endswith(b"\n"):  # a power cut left half a line: end it so the next batch starts clean
                with open(f, "ab") as fh:
                    fh.write(b"\n")

    @property
    def rows(self):
        return sum(self.counts.values())

    def _files(self):
        """Segments, oldest first; the live file is last."""
        rolled = sorted((f for f in self.path.parent.glob(self.path.name + ".*") if f.suffix[1:].isdigit()), key=lambda f: int(f.suffix[1:]))
        return rolled + ([self.path] if self.path.exists() else [])

    def _read(self, f):
        with open(f, "rb") as fh:
            for line in fh:
                try:
                    yield tuple(json.loads(line))
                except ValueError:
                    log.warning("spool %s: skipped an unreadable line (%d bytes), likely a write cut short", f.name, len(line))

    def append(self, rows):
        """Write a batch durably. Raises OSError if the disk refuses; the caller keeps the rows then."""
        if not rows:
            return
        data = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
        with self.lock:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes // 4:  # roll over so the oldest part can be dropped alone
                n = max([int(f.suffix[1:]) for f in self._files() if f != self.path] + [0]) + 1
                seg = self.path.with_name(f"{self.path.name}.{n}")
                os.replace(self.path, seg)
                self.counts[seg] = self.counts.pop(self.path, 0)
            with open(self.path, "ab") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())  # once per batch, not per reading
            self.counts[self.path] = self.counts.get(self.path, 0) + len(rows)
            while sum(f.stat().st_size for f in self._files()) > self.max_bytes and len(self._files()) > 1:
                old = self._files()[0]  # ponytail: never drops the live file, so one batch may overshoot max_bytes
                self.dropped += self.counts.pop(old, 0)
                old.unlink()

    def replay(self, add, batch=5000):
        """Send everything to `add(rows)`, oldest first, deleting each segment once it is stored. Raises whatever `add` raises."""
        with self.lock:
            for f in self._files():
                buf = []
                for r in self._read(f):
                    buf.append(r)
                    if len(buf) >= batch:
                        add(buf)
                        buf = []
                if buf:
                    add(buf)
                f.unlink()
                self.counts.pop(f, None)
