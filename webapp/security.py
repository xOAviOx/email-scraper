"""Password hashing with the standard library (no native build deps).

PBKDF2-HMAC-SHA256 with a per-password random salt. Format stored in the DB:
    <salt_hex>$<derived_key_hex>
"""

import hashlib
import hmac
import os

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), _ITERATIONS)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)
