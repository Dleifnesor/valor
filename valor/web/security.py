"""Primitives: Argon2id passwords, TOTP (RFC 6238), recovery codes, session tokens, AES-GCM, rate limits."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# RFC 9106 "second recommended option": t=3, m=64 MiB, p=4.
_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

PASSWORD_MIN = 12
PASSWORD_MAX = 256


# ---------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str | None, password: str) -> tuple[bool, str | None]:
    """(matches, new_hash_if_rehash_needed). Always does the same work, even for unknown users."""
    try:
        _ph.verify(stored or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if stored is None:
        return False, None
    return True, (_ph.hash(password) if _ph.check_needs_rehash(stored) else None)


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    if username and username.lower() in password.lower():
        return "The password must not contain the username."
    if len(set(password)) < 5:
        return "The password is too repetitive."
    return None


# ---------------------------------------------------------------------- TOTP (RFC 6238)
STEP = 30


def new_totp_secret() -> bytes:
    return secrets.token_bytes(20)


def b32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def totp_code(secret: bytes, step: int, digits: int = 6) -> str:
    mac = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    value = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{value:0{digits}d}"


def verify_totp(secret: bytes, code: str, last_step: int, now: float | None = None, window: int = 1) -> int | None:
    """Returns the matched time step (store it to block replays), or None."""
    code = (code or "").replace(" ", "").strip()
    if len(code) != 6 or not code.isdigit():
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - window, current + window + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret: bytes, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}", safe="")
    return (f"otpauth://totp/{label}?secret={b32(secret)}&issuer={quote(issuer, safe='')}"
            "&algorithm=SHA1&digits=6&period=30")


# ---------------------------------------------------------------------- recovery codes
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i


def new_recovery_codes(n: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(n)]


def normalize_recovery_code(code: str) -> str:
    c = "".join(ch for ch in (code or "").lower() if ch.isalnum())
    return f"{c[:5]}-{c[5:]}" if len(c) == 10 else ""


def hash_token(value: str) -> str:
    """For high-entropy random values only (session IDs, recovery codes): a plain SHA-256 is enough."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------- secrets at rest
class Box:
    """AES-256-GCM for secrets stored in the database (TOTP seeds, LDAP and SMTP passwords, webhook URLs).
    The context string is bound as associated data, so a ciphertext can't be moved to another field."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("secret key must be 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_file(cls, path: str | Path) -> "Box":
        return cls(Path(path).read_bytes()[:32])

    @staticmethod
    def create_key_file(path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            return
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(32))

    def seal(self, plaintext: bytes | str, context: str) -> bytes:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        nonce = secrets.token_bytes(12)
        return nonce + self._aead.encrypt(nonce, plaintext, context.encode())

    def open(self, blob: bytes, context: str) -> bytes:
        return self._aead.decrypt(blob[:12], blob[12:], context.encode())

    def seal_text(self, text: str, context: str) -> str:
        return base64.b64encode(self.seal(text, context)).decode()

    def open_text(self, data: str, context: str) -> str:
        return self.open(base64.b64decode(data), context).decode()


# ---------------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window counter per key (in memory: the web service runs as one process)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.time())) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._prune(key, now).append(now)
            if len(self._hits) > 10000:      # bound memory under a flood of distinct keys
                for k in [k for k, q in self._hits.items() if not q][:5000]:
                    self._hits.pop(k, None)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
