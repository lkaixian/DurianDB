"""An operator reads a sentence, never an exception class name or an errno tag. Run: python tests/test_error_sentences.py

`docs/12-error-handling.md` section 4 is the contract: what the console shows is what happened, in the
console's voice; the class name and the traceback belong in the log. This suite holds both halves: every
delivery failure is turned into an operator-safe line, and no server string puts a raw exception in front of
an operator.
"""
import re
import smtplib
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from durian.core import sentence  # noqa: E402
from durian import outputs  # noqa: E402

# what an operator must never have to read
FORBIDDEN = ("Traceback", 'File "', "Errno", "Error:", "Exception:", "ssl.SSLError", "socket.timeout")


def clean(text, why):
    assert isinstance(text, str) and text, why
    for bad in FORBIDDEN:
        assert bad not in text, (why, bad, text)
    # a class name is capitalised and ends in Error/Exception with no space in front of the sentence
    assert not re.search(r"\b[A-Z]\w*(Error|Exception|Interrupt)\b", text), (why, text)
    return text


# ---- the helper itself
assert sentence(None) == "" and sentence("not an exception") == ""
assert sentence(ValueError("the value is out of range")) == "the value is out of range"
assert sentence(ValueError("ValueError: the value is out of range")) == "the value is out of range"
assert sentence(OSError(2, "No such file or directory", "/etc/durian/creds.json")) == "No such file or directory", "the path stays in the log"
assert sentence(urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, None)) == "the server answered 503 Service Unavailable"
assert sentence(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))) == "Connection refused"
assert sentence(urllib.error.URLError(None)) == "the server could not be reached"
assert sentence(ValueError("x" * 500)) == "x" * 200, "one line, capped, so a wall of text cannot fill the console"
assert sentence(ValueError("  spaced\n\tout  ")) == "spaced out"

# ---- every delivery failure the console can show is one of those sentences
cases = [
    ConnectionRefusedError(111, "Connection refused"),
    TimeoutError("timed out"),
    urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    urllib.error.HTTPError("http://x", 500, "Internal Server Error", {}, None),
    smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no such user")}),
    smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
    smtplib.SMTPResponseException(550, b"5.1.1 no such user"),
    FileNotFoundError(2, "No such file or directory", "C:/lab/creds.json"),
    ValueError("ValueError: bad escape"),
    RuntimeError("paho reconnect failed"),
]
for ex in cases:
    clean(outputs._reason(ex), f"delivery reason for {type(ex).__name__}")
    clean(outputs._reason(urllib.error.URLError(ex)), f"delivery reason for a wrapped {type(ex).__name__}")

# a failure with nothing to say still reaches the operator as a sentence
assert outputs._reason(Exception()) == "delivery failed"

# ---- no server string puts a raw exception in front of an operator (the scan the helper's docstring promises)
BAD = [
    re.compile(r'type\(ex\)\.__name__'),          # a class name in a message
    re.compile(r'_note\([^\n]*\{ex\}'),           # a raw exception in what a handler row shows
    re.compile(r'\.error = f"[^\n]*\{ex\}'),      # a raw exception as a source's error
    re.compile(r'"error":\s*str\(e?x?\)'),        # a raw exception in a JSON payload
    re.compile(r'add\([^\n]*str\(ex\)'),          # a raw exception in a field error
]
offenders = []
for path in sorted((ROOT / "durian").glob("*.py")):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for pat in BAD:
            if pat.search(line) and "log." not in line:  # the log may name the class; the operator may not
                offenders.append(f"{path.name}:{n}: {line.strip()}")
                break
assert not offenders, "an operator-facing string names the exception:\n" + "\n".join(offenders)

print("ok")
