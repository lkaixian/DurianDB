"""Background jobs: one worker thread and a bounded queue, so a slow backup or a hanging device test does not hold an HTTP request.

`submit` returns an id at once. The caller asks `get` for the record: running, then done with a result, or error with a sentence.
Only the last `keep` jobs are remembered; an old one answers "unknown job" rather than growing without bound.
"""
import itertools
import logging
import queue
import threading
import time

from .core import sentence

log = logging.getLogger("durian.jobs")

QUEUE_MAX = 100
KEEP = 50


class Jobs:
    def __init__(self, keep=KEEP):
        self.keep = keep
        self.queue = queue.Queue(maxsize=QUEUE_MAX)
        self.records = {}  # id -> record; insertion order is job order
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, name, fn, owner=None):
        """Queue `fn` and return its id immediately. A full queue is recorded as a failed job, not raised at the caller."""
        jid = next(self.ids)
        rec = {"id": jid, "name": name, "state": "running", "result": None, "error": None,
               "started": time.time(), "finished": None, "owner": owner}
        with self.lock:
            self.records[jid] = rec
            while len(self.records) > self.keep:
                del self.records[next(iter(self.records))]
        try:
            self.queue.put_nowait((jid, fn))
        except queue.Full:
            self._finish(jid, error="too many jobs are waiting; try again when the list is shorter")
        return jid

    def get(self, jid):
        with self.lock:
            rec = self.records.get(jid)
            return dict(rec) if rec is not None else None

    def _finish(self, jid, result=None, error=None):
        with self.lock:
            rec = self.records.get(jid)
            if rec is not None:
                rec.update(state="error" if error is not None else "done", result=result, error=error, finished=time.time())

    def _work(self):
        while True:
            jid, fn = self.queue.get()
            try:
                self._finish(jid, result=fn())
            except Exception as e:  # the failure is the result: an operator gets a sentence, never only a log line
                log.warning("job %s failed: %s", jid, e)
                self._finish(jid, error=sentence(e) or "the job failed without saying why")
            finally:
                self.queue.task_done()
