"""Operator commands."""

from __future__ import annotations

import base64

import pytest

from app import cli
from app.application.audit import AuditLog
from app.domain.audit import ChainReport


def test_generate_kek_prints_a_32_byte_key(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["generate-kek"]) == 0

    assert len(base64.b64decode(capsys.readouterr().out.strip())) == 32


def test_rewrap_keys_runs_against_configured_storage(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["rewrap-keys"]) == 0

    assert "rewrapped 0 data key(s)" in capsys.readouterr().out


def test_an_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        cli.main(["drop-everything"])


def test_verify_audit_reports_an_intact_chain(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["verify-audit"]) == 0

    assert "audit chain intact" in capsys.readouterr().out


def test_verify_audit_fails_loudly_on_tampering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    async def tampered(_self: object, _uow: object) -> ChainReport:
        return ChainReport(events_checked=3, first_broken_sequence=2, reason="event altered")

    monkeypatch.setattr(AuditLog, "verify", tampered)

    assert cli.main(["verify-audit"]) == 1
    assert "BROKEN at sequence 2" in capsys.readouterr().err
