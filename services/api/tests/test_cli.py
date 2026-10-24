"""Operator commands."""

from __future__ import annotations

import base64

import pytest

from app import cli


def test_generate_kek_prints_a_32_byte_key(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["generate-kek"]) == 0

    assert len(base64.b64decode(capsys.readouterr().out.strip())) == 32


def test_rewrap_keys_runs_against_configured_storage(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["rewrap-keys"]) == 0

    assert "rewrapped 0 data key(s)" in capsys.readouterr().out


def test_an_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        cli.main(["drop-everything"])
