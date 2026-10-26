"""Make a self-signed certificate and key for the console, for reaching it from another machine.

Usage: python tools/make_cert.py [hostname or IP ...]   (default: localhost and 127.0.0.1)
Writes tools/cert.pem and tools/cert.key, then run the server with
    DURIAN_HOST=0.0.0.0 DURIAN_TLS_CERT=tools/cert.pem DURIAN_TLS_KEY=tools/cert.key python -m durian.server
A self-signed certificate is not trusted by any browser: import it as a trusted root, or use a certificate
from your own certificate authority. Needs `cryptography` (the same extra the MQTT lab uses).
"""
import datetime as dt
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

OUT = Path(__file__).resolve().parent


def main(argv):
    hosts = argv or ["localhost", "127.0.0.1"]
    alts = [x509.IPAddress(ipaddress.ip_address(h)) if _ip(h) else x509.DNSName(h) for h in hosts]
    now = dt.datetime.now(dt.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alts), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    (OUT / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (OUT / "cert.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    print(f"Wrote {OUT / 'cert.pem'} and {OUT / 'cert.key'} for {', '.join(hosts)}.")


def _ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    main(sys.argv[1:])
