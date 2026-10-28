"""POP3 source: numbers parsed out of received e-mail. Each cycle fetches the messages not seen before (tracked by UIDL, in memory),
newest first, and a point takes the first capture of its `pattern` (one group) in the Subject or body of the newest message that has one.

Mail is only deleted from the server when the source says `delete: true`. Patterns are checked at save (length, one group,
a safe subset, see _unsafe) and matched against the first MAX_TEXT characters.
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
from .oidc import _localhost
from .polled import PolledSource, number, scale

MAX_MESSAGES, MAX_TEXT, MAX_PATTERN, MAX_UNBOUNDED = 20, 2000, 200, 2  # MAX_TEXT: the prefix of Subject/body a pattern is run on
SECURITY = ("ssl", "starttls", "none")
_REPEATS = (sre.MAX_REPEAT, sre.MIN_REPEAT)
_BANNED = (sre.GROUPREF, sre.GROUPREF_EXISTS, sre.ASSERT, sre.ASSERT_NOT)


def _ops(node):
    """Every (op, args) in a parsed pattern, nested ones included."""
    for op, av in node:
        yield op, av
        for a in av if isinstance(av, (tuple, list)) else (av,):
            for sub in a if isinstance(a, list) else (a,):
                if isinstance(sub, sre.SubPattern):
                    yield from _ops(sub)


def _unsafe(tree):
    """Why a pattern is refused, or None. ponytail: a conservative subset, because stdlib re has no timeout and a bad pattern holds the GIL.
    At most MAX_UNBOUNDED unbounded repeats, no alternation or repeat inside a repeat that can run twice, no backreference or lookaround.
    Ceiling: two unbounded repeats are quadratic at worst, which MAX_TEXT keeps to milliseconds. Raise either only with a new timing test."""
    ops = list(_ops(tree))
    if any(op in _BANNED for op, _ in ops):
        return "backreferences and lookarounds are not allowed"
    if sum(op in _REPEATS and av[1] == sre.MAXREPEAT for op, av in ops) > MAX_UNBOUNDED:
        return f"use at most {MAX_UNBOUNDED} open-ended repeats (*, + or {{n,}})"
    if any(op in _REPEATS and av[1] > 1 and any(o in _REPEATS + (sre.BRANCH,) for o, _ in _ops(av[2])) for op, av in ops):
        return "a repeated group cannot contain a | or another repeat, such as (a+)+ or (a|b)*"
    return None


def validate_source(b, add):
    if not (isinstance(b.get("host"), str) and b["host"]):
        add("host", "give the mail server's host name")
    if "port" in b and not (isinstance(b["port"], int) and not isinstance(b["port"], bool) and 1 <= b["port"] <= 65535):
        add("port", "must be a whole number from 1 to 65535")
    if b.get("security", "ssl") not in SECURITY:
        add("security", f"must be one of {', '.join(SECURITY)}")
    elif b.get("security") == "none" and not _localhost(b.get("host")):
        add("security", "none is allowed only for localhost; use ssl or starttls")
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
    elif why := _unsafe(tree):
        add("pattern", f"{why}; a pattern that can hang the matcher is refused")


class _TooBig(ValueError):
    pass


class _Capped:
    """The connection's reader, counting bytes as they arrive so a server that lies in LIST cannot make a RETR fill memory."""
    def __init__(self, f):
        self.f, self.left = f, float("inf")

    def readline(self, n=-1):
        line = self.f.readline(n)
        self.left -= len(line)
        if self.left < 0:
            raise _TooBig("a message was larger than max_bytes and was skipped")
        return line

    def __getattr__(self, name):
        return getattr(self.f, name)


class Pop3Source(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 60.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.cfg, self.seen, self.held = cfg, set(), {}

    def _connect(self):
        c, t = self.cfg, self.cfg.get("timeout", 10.0)
        host, sec = c["host"], c.get("security", "ssl")
        if sec == "ssl":
            return poplib.POP3_SSL(host, c.get("port", 995), timeout=t, context=ssl.create_default_context())
        conn = poplib.POP3(host, c.get("port", 110), timeout=t)
        if sec == "starttls":
            conn.stls(context=ssl.create_default_context())
        conn.file = _Capped(conn.file)
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
                conn.file.left = limit
                try:
                    raw = conn.retr(n)[1]
                except _TooBig:
                    self.seen.add(uids[n - 1])  # skipped for good, or the same lie would fail every cycle
                    raise
                texts = self._texts(b"\n".join(raw))
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
