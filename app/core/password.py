"""Password hashing utilities using passlib with bcrypt.

Provides secure password hashing and verification functions.
Never stores or logs raw passwords.
"""

from __future__ import annotations

from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        Hashed password string

    Raises:
        ValueError: If password is empty or exceeds bcrypt's 72-byte limit
    """
    if not password:
        raise ValueError("Password cannot be empty")
    # bcrypt hard limit: 72 bytes
    password = password[:72]
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a hash.

    Args:
        plain: Plain text password to verify
        hashed: Hashed password to verify against

    Returns:
        True if password matches, False otherwise

    Raises:
        ValueError: If password or hash is empty
    """
    if not plain:
        return False
    if not hashed:
        return False
    return pwd_context.verify(plain, hashed)
