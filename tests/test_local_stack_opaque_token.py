"""Opaque readiness credentials never inspect claims or fall back to login."""

import io
import sys

import pytest

from tools import local_stack


def test_stdin_mode_reaches_readiness_without_login_or_token_retention(tmp_path, monkeypatch, capsys):
    class ReadinessObserved(Exception):
        pass

    def forbidden(*args, **kwargs):
        raise AssertionError("Opaque mode must not access login or start a test service")

    async def readiness(stack, bearer):
        assert bearer == "Bearer opaque-fixture"
        raise ReadinessObserved

    for name in ("cached_token", "token_path", "login"):
        monkeypatch.setattr(local_stack, name, forbidden)
    monkeypatch.setattr(local_stack.subprocess, "Popen", forbidden)
    monkeypatch.setattr(local_stack, "private_dir", lambda path: tmp_path)
    monkeypatch.setattr(local_stack, "check_ports", lambda ports: None)
    monkeypatch.setattr(local_stack, "prepare", lambda *args, **kwargs: [])
    monkeypatch.setattr(local_stack, "check_stack", readiness)
    monkeypatch.setattr(sys, "argv", ["local_stack", "--id-token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("opaque-fixture\n"))
    with pytest.raises(ReadinessObserved):
        local_stack.main()
    assert "opaque-fixture" not in "".join(capsys.readouterr())
    assert not any(b"opaque-fixture" in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


@pytest.mark.parametrize("value", ["opaque-fixture", "opaque-fixture\n", "opaque-fixture\r\n"])
def test_opaque_stdin_token_keeps_the_unparsed_value(monkeypatch, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("Opaque mode must not use a login cache or interactive login")

    monkeypatch.setattr(local_stack, "cached_token", forbidden)
    monkeypatch.setattr(local_stack, "token_path", forbidden)
    monkeypatch.setattr(local_stack, "login", forbidden)
    monkeypatch.setattr(sys, "stdin", io.StringIO(value))
    assert local_stack.opaque_stdin_token() == "opaque-fixture"


@pytest.mark.parametrize("value", [
    "", "\n", " ", "opaque fixture", "opaque\nfixture", "x" * 65537,
    "sensitive-sentinel\x00value", "sensitive-sentinel\x01value",
    "sensitive-sentinel\x1fvalue", "sensitive-sentinel\x7fvalue",
    "sensitive-sentinelévalue", "sensitive-sentinel\u2028value",
])
def test_invalid_stdin_stops_before_preparation_or_auth_fallback(monkeypatch, value, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid stdin must stop before preparation or login")

    for name in ("prepare", "private_dir", "cached_token", "token_path", "login"):
        monkeypatch.setattr(local_stack, name, forbidden)
    monkeypatch.setattr(sys, "argv", ["local_stack", "--id-token-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(value))
    with pytest.raises(SystemExit) as error:
        local_stack.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "opaque fixture" not in captured.err
    assert "x" * 100 not in captured.err
    assert "sensitive-sentinel" not in captured.err
    assert not captured.out


@pytest.mark.parametrize("other", [["--login"], ["--email", "fixture@example.invalid"], ["--refresh-ui"]])
def test_stdin_auth_cannot_mix_with_cache_or_refresh_modes(monkeypatch, other):
    monkeypatch.setattr(sys, "argv", ["local_stack", "--id-token-stdin", *other])
    monkeypatch.setattr(sys, "stdin", io.StringIO("opaque-fixture"))
    with pytest.raises(SystemExit) as error:
        local_stack.main()
    assert error.value.code == 2
