import time
from app.auth.session import sign_session, verify_session


def test_sign_and_verify():
    token = sign_session("alice123", "secretkey", ttl_seconds=3600)
    assert verify_session(token, "secretkey") == "alice123"


def test_verify_wrong_secret():
    token = sign_session("alice123", "secretkey")
    assert verify_session(token, "wrong") is None


def test_verify_expired():
    token = sign_session("alice123", "secretkey", ttl_seconds=-1)
    assert verify_session(token, "secretkey") is None


def test_verify_tampered():
    token = sign_session("alice123", "secretkey")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert verify_session(tampered, "secretkey") is None