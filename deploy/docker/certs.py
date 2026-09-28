"""Own CA + server certificate for the platform container (uses ``cryptography``, no openssl binary needed).

    python certs.py --dir /data/tls --hosts 192.168.1.50,bikestation.local,127.0.0.1,localhost

* CA (``ca.key``/``ca.crt``, EC P-256, 10 years) is created once and kept.
* The server certificate (825 days) is renewed when the host list changes or it expires within 30 days.
* Prints the SHA-256 fingerprint of the CA (compare it in the browser and pass it to agents).
Same layout as ``deploy/install-server.sh`` uses with openssl on the LXC.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ORG = "Smart Bike Station"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_key(path: Path, key) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))


def _load_key(path: Path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def ensure_ca(d: Path) -> x509.Certificate:
    key_p, crt_p = d / "ca.key", d / "ca.crt"
    if key_p.exists() and crt_p.exists():
        return x509.load_pem_x509_certificate(crt_p.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"{ORG} local CA (docker)")])
    now = _now()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(key_p, key)
    crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "server.crt").unlink(missing_ok=True)  # new CA -> new server certificate
    return cert


def _san(hosts: list[str]) -> list[x509.GeneralName]:
    out: list[x509.GeneralName] = []
    for h in hosts:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            out.append(x509.DNSName(h))
    return out


def ensure_server(d: Path, ca: x509.Certificate, hosts: list[str]) -> bool:
    """Returns True when a new certificate was issued."""
    crt_p, key_p, san_p = d / "server.crt", d / "server.key", d / "server.san"
    wanted = ",".join(hosts)
    if crt_p.exists() and key_p.exists() and san_p.exists() and san_p.read_text() == wanted:
        cur = x509.load_pem_x509_certificate(crt_p.read_bytes())
        if cur.not_valid_after_utc > _now() + dt.timedelta(days=30):
            return False
    ca_key = _load_key(d / "ca.key")
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
                                     x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(_san(hosts)), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(key_p, key)
    crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    san_p.write_text(wanted)
    return True


def fingerprint(cert: x509.Certificate) -> str:
    return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--hosts", required=True, help="comma-separated IPs / DNS names (first = common name)")
    a = ap.parse_args()
    a.dir.mkdir(parents=True, exist_ok=True)
    hosts = list(dict.fromkeys(h.strip() for h in a.hosts.split(",") if h.strip()))
    ca = ensure_ca(a.dir)
    if ensure_server(a.dir, ca, hosts):
        print(f"Issued server certificate for: {', '.join(hosts)}")
    print(f"CA fingerprint: SHA-256 {fingerprint(ca)}")


if __name__ == "__main__":
    main()
