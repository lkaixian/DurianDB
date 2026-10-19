"""Create an isolated TLS Mosquitto lab under lab/mqtt: CA, server cert, password file, config.

Does not touch any broker already running on this machine (uses port 18883).
Run:   python tools/mqtt_lab.py
Start: "C:\\Program Files\\mosquitto\\mosquitto.exe" -c lab/mqtt/mosquitto.conf
"""
import datetime as dt
import ipaddress
import json
import secrets
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent.parent / "lab" / "mqtt"
MOSQ = Path(r"C:\Program Files\mosquitto")
PORT = 18883


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def pem_key(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("DurianDB lab CA")).issuer_name(name("DurianDB lab CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now).not_valid_after(now + dt.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    srv = (x509.CertificateBuilder().subject_name(name("localhost")).issuer_name(ca.subject)
           .public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now).not_valid_after(now + dt.timedelta(days=365))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
           .sign(ca_key, hashes.SHA256()))
    (ROOT / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (ROOT / "server.pem").write_bytes(srv.public_bytes(serialization.Encoding.PEM))
    (ROOT / "server.key").write_bytes(pem_key(key))

    creds = {"host": "localhost", "port": PORT, "user": "durian", "password": secrets.token_urlsafe(16),
             "ca": str(ROOT / "ca.pem")}
    (ROOT / "creds.json").write_text(json.dumps(creds, indent=2))
    subprocess.run([str(MOSQ / "mosquitto_passwd.exe"), "-b", "-c", str(ROOT / "passwd"),
                    creds["user"], creds["password"]], check=True)

    p = lambda f: (ROOT / f).as_posix()
    (ROOT / "mosquitto.conf").write_text(f"""listener {PORT} 127.0.0.1
cafile {p('ca.pem')}
certfile {p('server.pem')}
keyfile {p('server.key')}
tls_version tlsv1.2
allow_anonymous false
password_file {p('passwd')}
persistence false
""")
    print("lab ready in", ROOT)


if __name__ == "__main__":
    main()
