"""Password hashing.

The resident CSV carries plaintext passwords. They are hashed on the way in and
the hash is never selected by any read path - see `PASSWORD_FIELD` and the
projection applied in `app.services.resources`.
"""

from __future__ import annotations

import bcrypt

PASSWORD_FIELD = "passwordHash"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
