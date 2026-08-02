from app.auth.passwords import hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h) is True


def test_verify_wrong_password():
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False


def test_hash_is_unique_per_call():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2