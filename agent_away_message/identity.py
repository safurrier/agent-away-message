"""Opaque, locally stable identities for lifecycle records."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Final

_KEY_BYTES: Final = 32
_CREATION_RETRIES: Final = 20
_CREATION_RETRY_SECONDS: Final = 0.005


class IdentityError(ValueError):
    """Raised when a hook omits a usable session identity."""


def digest_session_id(value: object, key_path: Path) -> str:
    """Return a stable, non-reversible local digest; never persist source identity."""
    if not isinstance(value, (str, int)) or not str(value):
        raise IdentityError("hook input requires a session identity")
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path.parent.chmod(0o700)
    key = _load_or_create_key(key_path)
    return hashlib.blake2s(str(value).encode(), key=key, digest_size=16).hexdigest()


def _load_or_create_key(key_path: Path) -> bytes:
    try:
        return _read_key(key_path)
    except FileNotFoundError:
        pass
    except IdentityError:
        return _read_created_key(key_path)
    key = os.urandom(_KEY_BYTES)
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_created_key(key_path)
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, key)
        os.fsync(descriptor)
    except OSError as error:
        raise IdentityError("local identity key could not be created") from error
    finally:
        os.close(descriptor)
    return key


def _read_created_key(key_path: Path) -> bytes:
    for _ in range(_CREATION_RETRIES):
        try:
            return _read_key(key_path)
        except FileNotFoundError:
            pass
        except IdentityError:
            # A concurrent winner may have created the file before its complete key
            # becomes visible. Retrying is limited and never substitutes a new key.
            pass
        time.sleep(_CREATION_RETRY_SECONDS)
    raise IdentityError("local identity key is unavailable")


def _read_key(key_path: Path) -> bytes:
    try:
        key = key_path.read_bytes()
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise
        raise IdentityError("local identity key is unavailable") from error
    if len(key) != _KEY_BYTES:
        raise IdentityError("local identity key is invalid")
    return key


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("identity key write was incomplete")
        offset += written
