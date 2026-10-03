"""Argon2id password hashing via ``argon2-cffi``."""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError

from realestate.domain.ports.security import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    """Argon2id with the library's current recommended parameters.

    Tests may pass cheaper parameters; :meth:`needs_rehash` then reports stored
    hashes as stale once the parameters are raised again.
    """

    def __init__(self, hasher: _Argon2 | None = None) -> None:
        self._hasher = hasher or _Argon2()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
