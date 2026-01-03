"""Generate a new encryption key for ENCRYPTION_KEY environment variable.

This script generates a Fernet encryption key that can be used to encrypt/decrypt
tokens stored in the database.

Usage:
    python scripts/generate_encryption_key.py

Then set the output as your ENCRYPTION_KEY environment variable.
"""

from cryptography.fernet import Fernet


def main() -> None:
    """Generate and display a new encryption key."""
    key = Fernet.generate_key()
    key_str = key.decode()

    print("=" * 80)
    print("ENCRYPTION_KEY")
    print("=" * 80)
    print(key_str)
    print("=" * 80)
    print("\nTo set this key as an environment variable:")
    print(f'\n  export ENCRYPTION_KEY="{key_str}"')
    print("\nOr add it to your .env file:")
    print(f"  ENCRYPTION_KEY={key_str}")
    print("\n⚠️  IMPORTANT:")
    print("  - Keep this key secure and backed up")
    print("  - If you lose this key, encrypted tokens cannot be decrypted")
    print("  - If you change this key, users will need to re-authenticate")
    print("  - Use the same key across all instances/environments")
    print("=" * 80)


if __name__ == "__main__":
    main()
