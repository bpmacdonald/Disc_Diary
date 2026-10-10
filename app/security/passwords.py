from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Defaults are Argon2id with the library's current recommended cost parameters.
_hasher = PasswordHasher()

# Verified against when a username doesn't exist, so unknown-user and
# wrong-password logins take about the same time.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def burn_dummy_verification(password: str) -> None:
    verify_password(_DUMMY_HASH, password)
