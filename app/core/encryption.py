"""Token encryption utilities using Fernet symmetric encryption.

Provides secure encryption/decryption for sensitive tokens stored in the database.
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger


class EncryptionError(Exception):
    """Raised when encryption/decryption operations fail."""


class EncryptionKeyError(EncryptionError):
    """Raised when decryption fails due to wrong encryption key.

    This typically occurs when ENCRYPTION_KEY is not set or changed,
    causing tokens encrypted with a different key to fail decryption.
    """


def _get_encryption_key() -> bytes:
    """Get encryption key from environment or generate a new one.

    Fernet keys are already base64-encoded strings, so we use them directly.

    Returns:
        Fernet encryption key as bytes

    Raises:
        EncryptionError: If key cannot be obtained or generated
    """
    key_env = os.getenv("ENCRYPTION_KEY")
    if key_env:
        try:
            # Fernet keys are already base64-encoded strings
            # Convert to bytes for Fernet constructor
            return key_env.encode()
        except Exception as e:
            logger.error(f"Failed to process ENCRYPTION_KEY from environment: {e}")
            raise EncryptionError("Invalid ENCRYPTION_KEY format. Must be a Fernet key (base64-encoded string).") from e

    # Generate a new key (for development only)
    logger.warning(
        "ENCRYPTION_KEY not set. Generating a new key (NOT suitable for production). "
        "Set ENCRYPTION_KEY environment variable with a Fernet key (use Fernet.generate_key())."
    )
    key = Fernet.generate_key()
    logger.warning(f"Generated encryption key: {key.decode()}")
    return key


# Initialize Fernet cipher with key (cached at module level)
_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    """Get or create Fernet cipher instance.

    Returns:
        Fernet cipher instance
    """
    global _cipher
    if _cipher is None:
        key = _get_encryption_key()
        _cipher = Fernet(key)
    return _cipher


def encrypt_token(token: str) -> str:
    """Encrypt a token string for secure storage.

    Args:
        token: Plain text token to encrypt

    Returns:
        Encrypted token as base64-encoded string

    Raises:
        EncryptionError: If encryption fails
    """
    try:
        cipher = _get_cipher()
        encrypted = cipher.encrypt(token.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    except Exception as e:
        logger.error(f"Token encryption failed: {e}")
        raise EncryptionError(f"Failed to encrypt token: {e}") from e


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an encrypted token string.

    Args:
        encrypted_token: Base64-encoded encrypted token

    Returns:
        Decrypted plain text token

    Raises:
        EncryptionKeyError: If decryption fails due to wrong encryption key
        EncryptionError: If decryption fails for other reasons
    """
    try:
        cipher = _get_cipher()
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_token.encode())
        decrypted = cipher.decrypt(encrypted_bytes)
        return decrypted.decode()
    except InvalidToken as e:
        error_msg = (
            "Token decryption failed: Wrong encryption key. "
            "This usually means ENCRYPTION_KEY environment variable is not set or has changed. "
            "If ENCRYPTION_KEY is not set, a new key is generated on each startup, "
            "which cannot decrypt tokens encrypted with previous keys. "
            "Set ENCRYPTION_KEY to the key used to encrypt existing tokens, "
            "or users will need to re-authenticate."
        )
        logger.error(error_msg)
        raise EncryptionKeyError(error_msg) from e
    except Exception as e:
        logger.error(f"Token decryption failed: {e}")
        raise EncryptionError(f"Failed to decrypt token: {e}") from e
