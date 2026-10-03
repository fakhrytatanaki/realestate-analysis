"""The argon2 adapter behind the ``PasswordHasher`` port."""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2

from realestate.infrastructure.security.argon2_hasher import Argon2PasswordHasher

#: Cheap parameters keep the test fast; production uses the library defaults.
CHEAP = Argon2(time_cost=1, memory_cost=8, parallelism=1)


def test_round_trip() -> None:
    hasher = Argon2PasswordHasher(CHEAP)
    stored = hasher.hash("s3cret-password")

    assert stored.startswith("$argon2id$")
    assert hasher.verify(stored, "s3cret-password")
    assert not hasher.verify(stored, "wrong-password")


def test_garbage_hash_never_verifies() -> None:
    hasher = Argon2PasswordHasher(CHEAP)

    assert not hasher.verify("not-a-hash", "anything")
    assert hasher.needs_rehash("not-a-hash")


def test_needs_rehash_when_parameters_rise() -> None:
    stored = Argon2PasswordHasher(CHEAP).hash("s3cret-password")

    assert not Argon2PasswordHasher(CHEAP).needs_rehash(stored)
    assert Argon2PasswordHasher(Argon2(time_cost=2, memory_cost=8, parallelism=1)).needs_rehash(
        stored
    )
