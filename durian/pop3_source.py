"""POP3 source: numbers parsed out of received e-mail. Each cycle fetches the messages not seen before (tracked by UIDL, in memory),
newest first, and a point takes the first capture of its `pattern` (one group) in the Subject or body of the newest message that has one.

Mail is only deleted from the server when the source says `delete: true`. Patterns are checked at save (length, one group,
no repeat inside a repeat) and matched against capped text.
"""
import email
import email.policy
import json
import math
import poplib
import re
import ssl
from pathlib import Path
from re import _parser as sre  # nested-repeat check walks the parsed pattern

from .core import sentence
from .polled import PolledSource, number, scale

MAX_MESSAGES, MAX_TEXT, MAX_PATTERN = 20, 20000, 200
SECURITY = ("none", "starttls", "ssl")
_REPEATS = (sre.MAX_REPEAT, sre.MIN_REPEAT)


def _nested(node, inside=False):
    """True when a repeat that can run more than once sits inside another such repeat (the shape of catastrophic backtracking)."""
    for op, av in node:
        if op in _REPEATS:
            many = av[1] > 1
            if (many and inside) or _nested(av[2], inside or many):
                return True
            continue
        for a in av if isinstance(av, (tuple, list)) else (av,):
            for sub in a if isinstance(a, list) else (a,):
                if isinstance(sub, sre.SubPattern) and _nested(sub, inside):
                    return True
    return False


def validate_source(b, add):
    if not (isinstance(b.get("host"), str) and b["host"]):
        add("host", "give the mail server's host name")
    if "port" in b and not (isinstance(b["port"], int) and not isinstance(b["port"], bool) and 1 <= b["port"] <= 65535):
        add("port", "must be a whole number from 1 to 65535")
    if b.get("security", "none") not in SECURITY:
        add("security", f"must be one of {', '.join(SECURITY)}")
    if not (isinstance(b.get("creds"), str) and Path(b["creds"]).is_file()):
        add("creds", "must be the path of a JSON file with user and password")
    if not isinstance(b.get("delete", False), bool):
        add("delete", "must be true or false")
    for k, lo, hi in (("period", 1, 86400), ("timeout", 0.5, 60), ("max_bytes", 1000, 1_000_000)):
        v = b.get(k)
        if k in b and not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi):
            add(k, f"must be a number from {lo} to {hi}")


def validate_point(b, add):
    p = b.get("pattern")
    if b.get("where", "subject") not in ("subject", "body"):
        add("where", "must be subject or body")
    if not (isinstance(p, str) and 0 < len(p) <= MAX_PATTERN):
        add("pattern", f"give a pattern of up to {MAX_PATTERN} characters with one capture group around the number")
        return
    try:
        rx = re.compile(p)
        tree = sre.parse(p)
    except (re.error, RecursionError, OverflowError) as e:
        add("pattern", f"the pattern is not valid: {getattr(e, 'msg', 'too complex')}")
        return
    if rx.groups != 1:
        add("pattern", "must have exactly one capture group around the number")
    elif _nested(tree):
        add("pattern", "a repeat inside a repeat, such as (a+)+, can hang the matcher; write it without nesting")


class Pop3Source(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 60.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.cfg, self.seen, self.held = cfg, set(), {}

    def _connect(self):
        c, t = self.cfg, self.cfg.get("timeout", 10.0)
        host, sec = c["host"], c.get("security", "none")
        if sec == "ssl":
            return poplib.POP3_SSL(host, c.get("port", 995), timeout=t, context=ssl.create_default_context())
        conn = poplib.POP3(host, c.get("port", 110), timeout=t)
        if sec == "starttls":
            conn.stls(context=ssl.create_default_context())
        return conn

    @staticmethod
    def _texts(raw):
        m = email.message_from_bytes(raw, policy=email.policy.default)
        part = m.get_body(preferencelist=("plain", "html"))
        try:
            body = part.get_content() if part else ""
        except (LookupError, ValueError):
            body = ""
        return {"subject": str(m.get("Subject", ""))[:MAX_TEXT], "body": body[:MAX_TEXT]}

    def fetch(self):
        creds = json.loads(Path(self.cfg["creds"]).read_text())
        try:
            conn = self._connect()
        except poplib.error_proto as e:
            raise ValueError("the mail server refused the connection") from e
        try:
            try:
                conn.user(creds["user"])
                conn.pass_(creds.get("password", ""))
                uids = [ln.decode().split()[1] for ln in conn.uidl()[1]]
                sizes = {int(ln.split()[0]): int(ln.split()[1]) for ln in conn.list()[1]}
            except poplib.error_proto as e:
                raise ValueError("the mail server refused the login or the request") from e
            self.seen &= set(uids)  # forget mail that has left the mailbox
            new = [n for n, u in enumerate(uids, 1) if u not in self.seen][-MAX_MESSAGES:]  # ponytail: older new mail beyond the cap is skipped
            limit, found, rejected = self.cfg.get("max_bytes", 100000), {}, []
            for n in reversed(new):  # newest first, so the first match per point wins
                if sizes.get(n, 0) > limit:
                    continue
                texts = self._texts(b"\n".join(conn.retr(n)[1]))
                self.seen.add(uids[n - 1])  # only after the retr: a timeout leaves the message to be fetched next cycle
                for p in self.points.values():
                    m = p.name not in found and re.search(p.meta["pattern"], texts[p.meta.get("where", "subject")])
                    if m:
                        try:
                            found[p.name] = scale(p, number(float(m.group(1))))
                        except ValueError:
                            rejected.append(f"{p.name}: matched text is not a finite number")
                if self.cfg.get("delete"):
                    conn.dele(n)
            conn.quit()
        except BaseException:
            conn.close()
            raise
        self.held.update(found)
        return {n: self.held[n] for n in self.points if n in self.held}, rejected + [
            f"{n}: no matching message yet" for n in self.points if n not in self.held]
