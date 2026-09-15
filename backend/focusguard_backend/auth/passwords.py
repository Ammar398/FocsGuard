"""Password hashing (spec section 51).

WHY ARGON2ID, NOT BCRYPT
------------------------
bcrypt silently truncates at 72 bytes. A user with a long passphrase gets less
security than they believe they have, and nothing warns anybody. Argon2id has no
such limit, is memory-hard against GPU attack, and won the Password Hashing
Competition - it is the current default recommendation.

WHY THE PARAMETERS ARE MODEST
-----------------------------
The defaults below use 64 MB and two passes. That is deliberate for this project:
the reference machine is an i5-6500 with 16 GB, and a login that ties up 256 MB
per concurrent request is a denial-of-service vector on hardware this size. These
are tunable, and a production deployment on better hardware should raise them.

TIMING
------
`verify_password` takes about the same time whether the user exists or not. The
caller must still hash against a dummy value when no user is found - otherwise a
fast rejection reveals which email addresses are registered.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.auth.passwords")

#: Argon2id parameters. Memory in KiB.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=64 * 1024,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

#: Hashed at import so `verify_password` costs the same for a missing user as for
#: a real one. Without this, a fast "no such user" reply enumerates accounts.
_DUMMY_HASH = _hasher.hash("focusguard-timing-equaliser")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def hash_password(password: str) -> str:
    """Hash a password for storage. The result includes its own parameters."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password, in constant-ish time whether the user exists or not."""
    if not password_hash:
        # No such user. Do the work anyway so the timing does not give it away.
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False

    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False
    except InvalidHashError:
        # A corrupt or legacy hash. Refuse rather than crash the login route.
        logger.warning("Stored password hash is unreadable; refusing login")
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now use.

    Lets a successful login transparently upgrade an old hash, so raising the
    cost later does not require a password reset for everyone.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> str | None:
    """Return a problem with the password, or None if it is acceptable.

    Deliberately minimal: length is the property that actually correlates with
    strength. Composition rules ("must contain a symbol") push people toward
    predictable substitutions and are no longer recommended.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip() == "":
        return "Password must not be only whitespace."
    return None
