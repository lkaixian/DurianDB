"""LDAP simple-bind sign-in over stdlib sockets (RFC 4511 section 4.2 BindRequest, BER per ITU-T X.690).

Only a BindRequest is written and only the BindResponse resultCode is read: definite lengths, sizes bounded.
ldaps:// uses the default TLS context; plain ldap:// is allowed only on localhost, as for plain HTTP elsewhere.
The password is never logged or put in an error.
"""
import socket
import ssl
import urllib.parse

from .oidc import ROLE, _localhost

TIMEOUT = 8
MAX_REPLY = 8192
SETTINGS = ("url", "bind_template", "default_roles", "enabled")
LDAP_HASH = "ldap"  # stored in place of a password hash: such an account signs in by bind only (never matches a scrypt hash)


class LDAPError(Exception):
    """The directory could not be asked (not a wrong password)."""


def escape_dn(value):
    """RFC 4514 section 2.4 escaping of an attribute value: the specials, leading '#' or space, trailing space, NUL."""
    out = "".join("\\00" if c == "\0" else "\\" + c if c in ',+"\\<>;=' else c for c in value)
    if out.startswith(("#", " ")):
        out = "\\" + out
    if out.endswith(" ") and not out.endswith("\\ "):
        out = out[:-1] + "\\ "
    return out


def bind_dn(template, user):
    return template.replace("{user}", escape_dn(user))


def _len(n):
    return bytes([n]) if n < 128 else bytes([0x80 | (n.bit_length() + 7) // 8]) + n.to_bytes((n.bit_length() + 7) // 8, "big")


def _tlv(tag, body):
    return bytes([tag]) + _len(len(body)) + body


def _read_tlv(buf, pos=0):
    """(tag, body_start, body_end) of the TLV at pos, or None while the buffer is short. Definite lengths only."""
    if len(buf) < pos + 2:
        return None
    tag, first = buf[pos], buf[pos + 1]
    if first < 0x80:
        start, size = pos + 2, first
    else:
        k = first & 0x7F
        if k == 0 or k > 3:  # 0x80 is the indefinite form; more than 3 length bytes is beyond MAX_REPLY
            raise LDAPError("the directory sent a reply DurianDB cannot read")
        if len(buf) < pos + 2 + k:
            return None
        start, size = pos + 2 + k, int.from_bytes(buf[pos + 2:pos + 2 + k], "big")
    if size > MAX_REPLY:
        raise LDAPError("the directory sent a reply that is too large")
    return (tag, start, start + size) if len(buf) >= start + size else None


def bind_request(dn, password, msg_id=1):
    body = _tlv(0x02, b"\x03") + _tlv(0x04, dn.encode()) + _tlv(0x80, password.encode())
    return _tlv(0x30, _tlv(0x02, bytes([msg_id])) + _tlv(0x60, body))


def parse_bind_response(buf, msg_id=1):
    """The resultCode, or None while the reply is incomplete."""
    outer = _read_tlv(buf)
    if outer is None:
        return None
    if outer[0] != 0x30:
        raise LDAPError("the directory sent a reply DurianDB cannot read")
    inner = buf[outer[1]:outer[2]]
    mid = _read_tlv(inner)
    op = _read_tlv(inner, mid[2]) if mid else None
    if not (mid and op and mid[0] == 0x02 and inner[mid[1]:mid[2]] == bytes([msg_id]) and op[0] == 0x61):
        raise LDAPError("the directory sent a reply DurianDB cannot read")
    code = _read_tlv(inner, op[1])
    if not (code and code[0] == 0x0A and 0 < code[2] - code[1] <= 4):
        raise LDAPError("the directory sent a reply DurianDB cannot read")
    return int.from_bytes(inner[code[1]:code[2]], "big")


def _target(url):
    u = urllib.parse.urlsplit(url)
    return u.scheme, u.hostname, u.port or (636 if u.scheme == "ldaps" else 389)


def bind(url, dn, password, timeout=TIMEOUT):
    """True when the directory accepts the bind. An empty password is refused here, before any network call:
    an empty-password simple bind is an anonymous bind, which many servers accept (RFC 4513 section 5.1.2)."""
    if not (isinstance(password, str) and password and isinstance(dn, str) and dn):
        return False
    scheme, host, port = _target(url)
    try:
        with socket.create_connection((host, port), timeout) as raw:
            s = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if scheme == "ldaps" else raw
            s.sendall(bind_request(dn, password))
            buf = b""
            while (code := parse_bind_response(buf)) is None:
                chunk = s.recv(1024)
                if not chunk or len(buf) > MAX_REPLY:
                    raise LDAPError("the directory closed the connection without answering")
                buf += chunk
    except (OSError, ssl.SSLError) as ex:
        raise LDAPError(f"the directory at {host} could not be reached: {ex.__class__.__name__}") from None
    return code == 0  # ponytail: any non-zero resultCode is "wrong password"; busy/unavailable (51/52) should raise LDAPError instead


def validate_ldap(body, known_roles=None):
    """The stored `ldap` settings block as [{property, message}], like validate_sso. known_roles: names default_roles may use."""
    if not isinstance(body, dict):
        return [{"property": "body", "message": "must be an object"}]
    errors = []

    def add(prop, message):
        errors.append({"property": prop, "message": message})

    for key in sorted(set(body) - set(SETTINGS)):
        add(key, f"unknown setting, expected one of {sorted(SETTINGS)}")
    url, ok = body.get("url"), False
    if isinstance(url, str) and 0 < len(url) <= 500 and url.isprintable():
        try:
            u = urllib.parse.urlsplit(url)
            ok = (u.scheme in ("ldap", "ldaps") and bool(u.hostname) and not (u.username or u.password or u.path.strip("/") or u.query or u.fragment)
                  and (u.scheme == "ldaps" or _localhost(u.hostname)))
            u.port  # noqa: B018 - raises ValueError on a bad port
        except ValueError:
            ok = False
    if not ok:
        add("url", "must be an ldaps:// URL with a host (plain ldap:// is allowed only on localhost)")
    tpl = body.get("bind_template")
    if not (isinstance(tpl, str) and 0 < len(tpl) <= 500 and tpl.isprintable() and tpl.count("{user}") == 1):
        add("bind_template", "must contain {user} once, e.g. uid={user},ou=people,dc=example,dc=com")
    roles = body.get("default_roles")
    if not (isinstance(roles, list) and 0 < len(roles) <= 20 and all(isinstance(r, str) and ROLE.match(r) for r in roles)):
        add("default_roles", "must list at least one role name")
    elif known_roles is not None and (unknown := sorted(set(roles) - set(known_roles))):
        add("default_roles", f"unknown role {', '.join(unknown)}; use a role an account already holds")
    if not isinstance(body.get("enabled", True), bool):
        add("enabled", "must be true or false")
    return errors


def ldap_config(store):
    """The stored block when it is valid and enabled, else None."""
    cfg = store.get_setting("ldap")
    return cfg if isinstance(cfg, dict) and not validate_ldap(cfg) and cfg.get("enabled", True) else None
