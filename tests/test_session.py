import time
from app.auth.session import sign_session, verify_session


def test_sign_and_verify():
    token = sign_session("alice123", "secretkey", ttl_seconds=3600)
    user_id, is_admin = verify_session(token, "secretkey")
    assert user_id == "alice123"
    assert is_admin is False


def test_sign_and_verify_admin():
    token = sign_session("alice123", "secretkey", ttl_seconds=3600, is_admin=True)
    user_id, is_admin = verify_session(token, "secretkey")
    assert user_id == "alice123"
    assert is_admin is True


def test_verify_wrong_secret():
    token = sign_session("alice123", "secretkey")
    assert verify_session(token, "wrong") == (None, False)


def test_verify_expired():
    token = sign_session("alice123", "secretkey", ttl_seconds=-1)
    assert verify_session(token, "secretkey") == (None, False)


def test_verify_tampered():
    token = sign_session("alice123", "secretkey")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert verify_session(tampered, "secretkey") == (None, False)