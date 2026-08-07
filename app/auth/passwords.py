from argon2 import PasswordHasher

_ph = PasswordHasher()

# Precomputed hash used to equalize login timing for unknown usernames —
# verifying against it costs the same ~100ms Argon2 burn as a real check,
# so attackers cannot distinguish valid from invalid usernames by latency.
_DUMMY_HASH = _ph.hash("timing-equalizer")


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, plain)
        return True
    except Exception:
        return False