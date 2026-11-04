# *locks every door twice* OwO valor/web/security.py holds the security building blocks of sign-in! ^w^
#   passwords        hashed with Argon2id (slow and memory-hungry on purpose, so stolen hashes are hard to crack)
#   TOTP             the 6-digit authenticator app codes (RFC 6238), with replay protection
#   recovery codes   one-time backup codes for when your phone is lost
#   tokens           random session ids, stored only as hashes
#   rate limiting    a sliding-window counter against password guessing
# *wags tail* Nothing here knows about HTTP or the database; auth.py and users.py use these pieces.
"""Primitives: Argon2id passwords, TOTP (RFC 6238), recovery codes, session tokens, AES-GCM, rate limits."""

# Lazy type hints UwU
from __future__ import annotations

# base64 (base32 for TOTP secrets), hashlib/hmac (hashes and the TOTP math), secrets (strong randomness), struct
# (numbers <-> bytes), threading (a lock for the rate limiter), time, deque (a fast queue of hit times) and quote
# (URL-escaping for the authenticator link) >w<
import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import deque
from urllib.parse import quote

# argon2-cffi: the password hasher and the errors its verify() can raise OwO
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Box is imported only so other modules can get it from here too ("re-exported"); the noqa tells the linter that
# the unused import is on purpose ^w^
from ..crypto import Box  # noqa: F401  (re-exported: secrets at rest)

# The Argon2id settings: 3 passes over 64 MiB of memory with 4 lanes, as RFC 9106 recommends. _DUMMY_HASH is a
# hash of random junk, used to waste the same time on unknown usernames (see verify_password) UwU
# RFC 9106 "second recommended option": t=3, m=64 MiB, p=4.
_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

# Allowed password lengths: at least 12, at most 256 characters >w<
PASSWORD_MIN = 12
PASSWORD_MAX = 256


# ---------------------------------------------------------------------- passwords
# hash_password: Argon2id-hash a password (a random salt is built in) OwO
def hash_password(password: str) -> str:
    return _ph.hash(password)


# verify_password: does `password` match the stored hash? Returns (matches, new hash or None). The new hash is
# given when the stored one used older/weaker settings, so the caller can upgrade it while it has the password ^w^
def verify_password(stored: str | None, password: str) -> tuple[bool, str | None]:
    """(matches, new_hash_if_rehash_needed). Always does the same work, even for unknown users."""
    # TIMING SAFETY *ears perk up*: for an unknown user (stored is None) we STILL run a full verify against the dummy
    # hash, so "unknown user" and "wrong password" take the same time and attackers can't tell them apart UwU
    try:
        _ph.verify(stored or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    # An unknown user never matches, even if the dummy hash somehow did >w<
    if stored is None:
        return False, None
    # Match! Plus a fresh hash when the settings changed ^w^
    return True, (_ph.hash(password) if _ph.check_needs_rehash(stored) else None)


# password_problem: why a new password isn't acceptable (a sentence for the person), or None when it's fine OwO
def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    # The password may not contain the username (in any upper/lower case) UwU
    if username and username.lower() in password.lower():
        return "The password must not contain the username."
    # Fewer than 5 DIFFERENT characters ("aaaaaaaaaaaa", "abababab...") is too repetitive >w<
    if len(set(password)) < 5:
        return "The password is too repetitive."
    return None


# ---- TOTP: the 6-digit codes from authenticator apps. A new code every STEP = 30 seconds ^w^
# ---------------------------------------------------------------------- TOTP (RFC 6238)
STEP = 30


# new_totp_secret: 20 random bytes (160 bits), the size RFC 4226 recommends for SHA-1 OwO
def new_totp_secret() -> bytes:
    return secrets.token_bytes(20)


# b32: the secret as base32 text without "=" padding, which is how authenticator apps want it typed/scanned UwU
def b32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


# totp_code: the code for one time step *does the math with tiny paws*:
#   1. HMAC-SHA1 of the step number (8 bytes, big-endian) with the secret
#   2. "dynamic truncation": the last nibble of the MAC picks where to read 4 bytes
#   3. drop the top bit, take the last `digits` decimal digits, pad with leading zeros >w<
def totp_code(secret: bytes, step: int, digits: int = 6) -> str:
    mac = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F                     # RFC 4226 dynamic truncation
    value = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{value:0{digits}d}"


# verify_totp: check a typed code. Returns the time step it matched (the caller stores it as last_step so the
# same code can never be used twice = replay protection), or None ^w^
def verify_totp(secret: bytes, code: str, last_step: int, now: float | None = None, window: int = 1) -> int | None:
    """Returns the matched time step (store it to block replays), or None. window: steps of clock drift accepted
    on either side; a step at or before last_step was used already."""
    # People type codes with spaces ("123 456"); remove them. Anything that isn't exactly 6 digits fails OwO
    code = (code or "").replace(" ", "").strip()
    if len(code) != 6 or not code.isdigit():
        return None
    # The current step = seconds since 1970 divided by 30 (tests can pass their own `now`) UwU
    current = int((now if now is not None else time.time()) // STEP)
    # Accept the steps around now (window=1: 30 s before and after, for clocks that are a bit off), but only steps
    # NEWER than the last used one. compare_digest compares in constant time, so timing leaks nothing >w<
    for step in range(current - window, current + window + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


# otpauth_uri: the "otpauth://" link shown as a QR code when setting up an authenticator app. The label is
# "Issuer:username"; the parameters say SHA-1, 6 digits, 30 s (the defaults every app supports) ^w^
def otpauth_uri(secret: bytes, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}", safe="")
    return (f"otpauth://totp/{label}?secret={b32(secret)}&issuer={quote(issuer, safe='')}"
            "&algorithm=SHA1&digits=6&period=30")


# ---- Recovery codes: one-time backup codes, made of easy-to-read characters (no 0/o, 1/l/i) OwO
# ---------------------------------------------------------------------- recovery codes
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i


# new_recovery_codes: ten codes like "abcde-fgh23" (10 random characters, 5 + dash + 5) UwU
def new_recovery_codes(n: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(n)]


# normalize_recovery_code: accept a code typed in any case with or without the dash/spaces, and turn it back into
# the stored "abcde-fgh23" form (or "" when it can't be one) >w<
def normalize_recovery_code(code: str) -> str:
    """A typed recovery code in its stored form (any case, with or without the dash or spaces)."""
    letters = "".join(char for char in (code or "").lower() if char.isalnum())
    return f"{letters[:5]}-{letters[5:]}" if len(letters) == 10 else ""


# hash_token: SHA-256 of a token. A plain fast hash is fine HERE because these values are long and random:
# nobody can guess them, unlike human passwords (which need Argon2) ^w^
def hash_token(value: str) -> str:
    """For high-entropy random values only (session IDs, recovery codes): a plain SHA-256 is enough."""
    return hashlib.sha256(value.encode()).hexdigest()


# new_token: 32 random bytes as URL-safe text, used as session ids OwO
def new_token() -> str:
    return secrets.token_urlsafe(32)


# ---- Rate limiting UwU
# ---------------------------------------------------------------------- rate limiting
# RateLimiter: remembers when each key (e.g. a client IP address) had hits, and says "blocked" once there were
# `limit` hits within the last `window` seconds. It lives in memory, which works because valor-web is one
# process >w<
class RateLimiter:
    """Sliding-window counter per key (in memory: the web service runs as one process)."""

    # limit/window, a dict of key -> deque of hit times, and a lock because requests run in several threads ^w^
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    # _prune: drop this key's hits older than the window (they're at the left of the deque, oldest first) OwO
    def _prune(self, key: str, now: float) -> deque:
        """The key's hits inside the window (older ones dropped)."""
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        return hits

    # blocked: has this key reached the limit within the window? UwU
    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.time())) >= self.limit

    # hit: record one hit for this key, now >w<
    def hit(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._prune(key, now).append(now)
            # Memory guard: if someone floods us with lots of DIFFERENT keys, drop up to 5000 keys that have no recent hits
            # left, so memory can't grow forever ^w^ *sweeps with tail*
            if len(self._hits) > 10000:      # bound memory under a flood of distinct keys
                for stale in [key for key, hits in self._hits.items() if not hits][:5000]:
                    self._hits.pop(stale, None)
