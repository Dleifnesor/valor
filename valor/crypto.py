"""Secrets at rest: AES-256-GCM with a 32-byte key file readable only by the 'valor' service account.

Used by the web service (TOTP seeds, LDAP/SMTP passwords, webhook URLs) and by the engine (range login
passwords). The context string is bound as associated data, so a ciphertext can't be moved to another field.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path


class Box:
    def __init__(self, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if len(key) != 32:
            raise ValueError("secret key must be 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_file(cls, path: str | Path) -> "Box":
        return cls(Path(path).read_bytes()[:32])

    @classmethod
    def try_file(cls, path: str | Path | None) -> "Box | None":
        """The key file's Box, or None when there is no usable key (or no 'cryptography' package)."""
        if not path:
            return None
        try:
            return cls.from_file(path)
        except (OSError, ValueError, ImportError):
            return None

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
