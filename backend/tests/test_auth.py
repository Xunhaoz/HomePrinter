import time

import pytest
from fastapi import HTTPException

from homeprinter.auth import MAX_FAILURES_PER_CLIENT, Auth
from homeprinter.config import Settings


def make(code="Kitten", days=30):
    return Auth(Settings(access_code=code, session_days=days))


def test_code_is_case_insensitive_and_trimmed():
    auth = make()
    assert auth.check_code(" kITTEN ", "a")
    assert not auth.check_code("kitte", "a")


def test_token_round_trip_and_tampering():
    auth = make()
    token = auth.new_token()
    assert auth.valid(token)
    expires, _, sig = token.partition(".")
    assert not auth.valid(f"{int(expires) + 1}.{sig}")
    assert not auth.valid(None)
    assert not auth.valid("garbage")


def test_changing_the_code_invalidates_sessions():
    assert not make("other").valid(make().new_token())


def test_expired_token_is_rejected(monkeypatch):
    auth = make(days=1)
    token = auth.new_token()
    monkeypatch.setattr(time, "time", lambda real=time.time: real() + 2 * 86400)
    assert not auth.valid(token)


def test_lockout_after_repeated_failures():
    auth = make()
    for _ in range(MAX_FAILURES_PER_CLIENT):
        assert not auth.check_code("wrong", "1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        auth.check_code("kitten", "1.2.3.4")
    assert exc.value.status_code == 429
    assert auth.check_code("kitten", "5.6.7.8")  # other clients are unaffected


def test_no_code_means_open():
    auth = make("")
    assert not auth.enabled
    assert auth.valid(None)
