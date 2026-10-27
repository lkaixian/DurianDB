"""HTTPS and host binding for the console server. Run: python tests/test_tls.py

The last section runs a real TLS handshake against the server. It needs `cryptography` to create a temporary
certificate; without it the run stops with a one-line sentence naming the package instead of a traceback.
"""
import datetime as dt
import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import warnings

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
except ImportError:
    raise SystemExit("tests/test_tls.py needs `cryptography` to create its temporary certificate: pip install cryptography")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian import server  # noqa: E402
from durian.app import App  # noqa: E402
from durian.store import Store  # noqa: E402

ADMIN_PASSWORD = "a-test-password-long-enough"


def temporary_certificate(directory):
    """Write a self-signed certificate for 127.0.0.1, its own CA, and its key into `directory`."""
    now = dt.datetime.now(dt.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                        x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(directory, "cert.pem"), os.path.join(directory, "cert.key")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return cert_path, key_path


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

# a first-run password too weak to accept is a sentence, not a traceback
r = subprocess.run([sys.executable, "-m", "durian.server"],
                   env={**os.environ, "DURIAN_DB": ":memory:", "DURIAN_ADMIN_PASSWORD": "weak"},
                   capture_output=True, text=True, timeout=60)
assert r.returncode != 0 and "cannot create the first administrator" in (r.stdout + r.stderr), r.stdout + r.stderr
assert "Traceback" not in r.stderr, r.stderr

# ---- a real handshake against the same make_server() path the console uses, on an ephemeral port
with tempfile.TemporaryDirectory() as tmp:
    cert_path, key_path = temporary_certificate(tmp)
    server.app = App(Store(), os.path.join(tmp, "backups"))
    server.app.auth.bootstrap(ADMIN_PASSWORD)
    server.TLS = True  # what main() does when it is given a certificate
    httpd = server.make_server("127.0.0.1", 0, cert_path, key_path)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client = ssl.create_default_context(cafile=cert_path)  # the certificate is its own CA
        deadline = time.time() + 5
        while True:  # bounded readiness: a connect may arrive just before serve_forever starts
            try:
                health = http.client.HTTPSConnection("127.0.0.1", port, context=client, timeout=5)
                health.connect()  # completes the handshake, so the version is known before the server closes
                negotiated = health.sock.version()
                health.request("GET", "/api/health")
                response = health.getresponse()
                break
            except (OSError, ssl.SSLError):
                health.close()
                if time.time() > deadline:
                    raise
                time.sleep(0.05)
        assert response.status == 200, response.status
        assert negotiated in ("TLSv1.2", "TLSv1.3"), negotiated
        assert response.getheader("Strict-Transport-Security") == "max-age=31536000"
        assert json.loads(response.read())["ok"] is True
        health.close()

        # a real sign-in over TLS carries a cookie a browser only returns over HTTPS, and never to script
        login = http.client.HTTPSConnection("127.0.0.1", port, context=client, timeout=5)
        login.request("POST", "/api/login", body=json.dumps({"username": "admin", "password": ADMIN_PASSWORD}),
                      headers={"Content-Type": "application/json", "X-Requested-With": "durian"})
        response = login.getresponse()
        assert response.status == 200, response.status
        cookie = response.getheader("Set-Cookie") or ""
        assert "Secure" in cookie and "HttpOnly" in cookie, cookie
        response.read()
        login.close()

        # TLS 1.0 and 1.1 need their legacy ciphers enabled explicitly on current OpenSSL builds. If this
        # build cannot offer them, no packet reaches the server, so say so instead of asserting a refusal
        # the client never gave the server a chance to make.
        legacy = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        legacy.check_hostname = False
        legacy.verify_mode = ssl.CERT_NONE
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # TLS 1.0 and 1.1 are offered on purpose here
            legacy.minimum_version = ssl.TLSVersion.TLSv1
            legacy.maximum_version = ssl.TLSVersion.TLSv1_1
        try:
            legacy.set_ciphers("ALL:@SECLEVEL=0")
        except (ssl.SSLError, ValueError) as ex:
            print(f"ok (skipped the TLS-below-1.2 check: this OpenSSL build cannot offer it: {ex})")
        else:
            conn = None
            try:
                conn = legacy.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5))
            except ssl.SSLError:
                pass  # the server refused the protocol, which is the point of the check
            if conn:
                conn.close()
            assert conn is None, "the server accepted TLS below 1.2"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()

print("ok")
