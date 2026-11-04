"""Symmetric encryption for secrets stored in config / .env files.

Values wrapped in ``ENC(...)`` are encrypted with Fernet (from the
``cryptography`` package). Anything not wrapped passes through
untouched, so encrypted and plain values can mix freely.

    from apps.workflow_app.utils.crypto import encrypt_value, decrypt_value

    key = generate_key()                      # store it safely, not in the repo
    encrypted = encrypt_value("s3cr3t", key=key)   # -> "ENC(...)"
    decrypt_value(encrypted, key=key)              # -> "s3cr3t"
    decrypt_value("plain", key=key)                # -> "plain"

When ``key`` is omitted, :func:`get_encryption_key` supplies it from
``APP_ENCRYPTION_KEY`` or ``APP_ENCRYPTION_KEY_FILE``.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "decrypt_value",
    "encrypt_value",
    "generate_key",
    "get_encryption_key",
    "is_encrypted",
]

_PREFIX = "ENC("
_SUFFIX = ")"


def generate_key():
    """Generate a new random Fernet key (urlsafe base64 string)."""
    return Fernet.generate_key().decode("ascii")


def get_encryption_key():
    """Return the encryption key from the environment.

    Checks ``APP_ENCRYPTION_KEY`` first, then the file named by
    ``APP_ENCRYPTION_KEY_FILE`` (default ``.secret.key``).
    Raises ValueError when no key is available.
    """
    key = os.environ.get("APP_ENCRYPTION_KEY")
    if key:
        return key.strip()
    key_file = os.environ.get("APP_ENCRYPTION_KEY_FILE", ".secret.key")
    if key_file and os.path.isfile(key_file):
        with open(key_file, encoding="utf-8") as fh:
            return fh.read().strip()
    raise ValueError(
        "no encryption key available: set APP_ENCRYPTION_KEY "
        "or APP_ENCRYPTION_KEY_FILE"
    )


def _fernet(key):
    if key is None:
        key = get_encryption_key()
    if isinstance(key, str):
        key = key.encode("utf-8")
    return Fernet(key)


def encrypt_value(value, key=None):
    """Encrypt ``value`` and wrap it as ``ENC(...)``."""
    token = _fernet(key).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}{_SUFFIX}"


def is_encrypted(value):
    """True when ``value`` is a string wrapped in ``ENC(...)``."""
    return (
        isinstance(value, str)
        and value.startswith(_PREFIX)
        and value.endswith(_SUFFIX)
    )


def decrypt_value(value, key=None):
    """Decrypt an ``ENC(...)`` value; non-wrapped values pass through.

    Raises ValueError when the value is wrapped but cannot be
    decrypted with the key (wrong key or corrupted data).
    """
    if not is_encrypted(value):
        return value
    token = value[len(_PREFIX):-len(_SUFFIX)]
    try:
        return _fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("cannot decrypt value: wrong key or corrupted data") from exc
