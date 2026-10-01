"""At-rest encryption for stored SSH credentials.

Credentials (passwords / private keys) are encrypted with Fernet before they
touch the database and decrypted only in-memory at dispatch time. The Fernet
key is derived from ``settings.ssh_cred_key`` so any human-readable secret can
be supplied via the SSH_CRED_KEY env var.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # Derive a stable 32-byte urlsafe key from the configured secret.
    digest = hashlib.sha256(get_settings().ssh_cred_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential. Returns urlsafe base64 ciphertext."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a credential produced by :func:`encrypt_secret`."""
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
