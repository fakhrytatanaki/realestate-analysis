"""Port for password hashing.

The domain only needs to know that a hash can be made and checked; which
algorithm and which library is an infrastructure decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PasswordHasher(ABC):
    """One-way, salted password hashing."""

    @abstractmethod
    def hash(self, password: str) -> str:
        """Hash ``password`` with a fresh salt; the result embeds its parameters."""

    @abstractmethod
    def verify(self, password_hash: str, password: str) -> bool:
        """Whether ``password`` matches. Never raises for a mismatch."""

    @abstractmethod
    def needs_rehash(self, password_hash: str) -> bool:
        """Whether the hash was made with weaker parameters than current ones."""
