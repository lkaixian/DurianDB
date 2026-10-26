"""HTTPS and host binding for the console server. Run: python tests/test_tls.py"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian import server  # noqa: E402

# the cookie is Secure only under HTTPS, so a browser never hands it back over plain HTTP
server.TLS = False
assert "Secure" not in server.session_cookie("abc", 60)
server.TLS = True
assert server.session_cookie("abc", 60) == "durian_session=abc; HttpOnly; SameSite=Strict; Path=/; Max-Age=60; Secure"
assert "; Secure" in server.session_cookie("", 0)  # logging out under HTTPS still clears it
server.TLS = False

# binding beyond this machine without a certificate is refused instead of serving a password in clear
env = {**os.environ, "DURIAN_HOST": "0.0.0.0", "DURIAN_DB": ":memory:"}
r = subprocess.run([sys.executable, "-m", "durian.server"], env=env, capture_output=True, text=True, timeout=60)
assert r.returncode != 0 and "DURIAN_TLS_CERT" in (r.stdout + r.stderr), r.stdout + r.stderr
assert "http://0.0.0.0" not in r.stdout

# a certificate without its key is a clear error, not a traceback
r = subprocess.run([sys.executable, "-m", "durian.server"], env={**env, "DURIAN_TLS_CERT": "x.pem"},
                   capture_output=True, text=True, timeout=60)
assert r.returncode != 0 and "DURIAN_TLS_KEY" in (r.stdout + r.stderr), r.stdout + r.stderr

print("ok")
