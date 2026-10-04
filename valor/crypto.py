# *whispers* OwO this is valor/crypto.py, where secrets get tucked away safe and snug >w<
# Some things VALOR stores are secret: authenticator (TOTP) seeds, LDAP/SMTP passwords, webhook URLs, and the
# login passwords of range VMs. They're never stored in plain text; this file seals them with AES-256-GCM
# (authenticated encryption: it hides the data AND detects any tampering) using a 32-byte key file that only the
# 'valor' service account can read ^w^ *curls tail protectively around the key*
# The "context" string (e.g. "user:5:totp") is bound in as associated data: a sealed value only opens with the
# same context, so nobody can copy a ciphertext from one field into another and have it decrypt.
"""Secrets at rest: AES-256-GCM with a 32-byte key file readable only by the 'valor' service account.

Used by the web service (TOTP seeds, LDAP/SMTP passwords, webhook URLs) and by the engine (range login
passwords). The context string is bound as associated data, so a ciphertext can't be moved to another field.
"""

# Lazy type hints UwU
from __future__ import annotations

# base64 turns sealed bytes into text for the database; os opens files with exact permissions; secrets gives
# cryptographically strong random bytes; Path handles file paths OwO
import base64
import os
import secrets
from pathlib import Path


# Box: a little locked box *paws at it*. Make one from a key, then seal (encrypt) and open (decrypt) values ^w^
class Box:
    # The constructor takes the raw 32-byte key. The `cryptography` import is done here (not at the top of the
    # file) so modules that import crypto.py still load on machines without that package (the installer, tests) >w<
    def __init__(self, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        # AES-256 needs exactly 32 bytes (256 bits) of key; anything else is refused loudly UwU
        if len(key) != 32:
            raise ValueError("secret key must be 32 bytes")
        # The AES-GCM cipher object, kept private (leading underscore) *hides it behind tail*
        self._aead = AESGCM(key)

    # from_file: build a Box from the key file. Only the first 32 bytes are used, so a trailing newline from an
    # editor can't break it ^w^
    @classmethod
    def from_file(cls, path: str | Path) -> "Box":
        return cls(Path(path).read_bytes()[:32])

    # try_file: like from_file, but returns None instead of raising when there is no path, no readable file, a wrong
    # key length, or no `cryptography` package. Callers then simply run without encryption features OwO
    @classmethod
    def try_file(cls, path: str | Path | None) -> "Box | None":
        """The key file's Box, or None when there is no usable key (or no 'cryptography' package)."""
        if not path:
            return None
        try:
            return cls.from_file(path)
        except (OSError, ValueError, ImportError):
            return None

    # create_key_file: make a new random key file, but only if there isn't one already (never overwrite a key:
    # that would make every stored secret unreadable!) >w< *gasps*
    @staticmethod
    def create_key_file(path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            return
        # O_CREAT|O_EXCL = create, and fail if it exists (no race with another process); mode 0o640 = owner may read and
        # write, the group may read, everyone else gets nothing ^w^
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        # Write 32 strong random bytes = the key *sprinkles randomness*
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(32))

    # seal: encrypt `plaintext` for one `context`. Text is turned into bytes first (UTF-8) UwU
    def seal(self, plaintext: bytes | str, context: str) -> bytes:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        # A fresh random 12-byte nonce for EVERY seal (GCM must never reuse a nonce with the same key). The result is
        # nonce + ciphertext (which ends in GCM's 16-byte authentication tag), so open() can find the nonce again >w<
        nonce = secrets.token_bytes(12)
        return nonce + self._aead.encrypt(nonce, plaintext, context.encode())

    # open: split the blob back into the nonce (first 12 bytes) and the ciphertext, then decrypt. If the data was
    # changed or the context differs, decrypt raises instead of returning garbage *ears perk up* ^w^
    def open(self, blob: bytes, context: str) -> bytes:
        return self._aead.decrypt(blob[:12], blob[12:], context.encode())

    # seal_text / open_text: the same, but with base64 text in and out, which is what gets stored in SQLite and
    # config files OwO *happy wiggle*
    def seal_text(self, text: str, context: str) -> str:
        return base64.b64encode(self.seal(text, context)).decode()

    def open_text(self, data: str, context: str) -> str:
        return self.open(base64.b64decode(data), context).decode()
