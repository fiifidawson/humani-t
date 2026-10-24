"""Operator commands."""

from __future__ import annotations

import base64
import io

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


class TestCreateAdmin:
    """The first administrator is created from inside the deployment, never over HTTP."""

    def test_an_admin_is_created_from_a_password_on_stdin(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr("sys.stdin", io.StringIO("violet-harbour-quietly-41-lanterns\n"))

        assert (
            cli.main(["create-admin", "--email", "admin@example.org", "--password-stdin"]) == 0
        )
        assert "created admin" in capsys.readouterr().out

    def test_a_weak_password_is_refused_with_the_reasons(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr("sys.stdin", io.StringIO("password\n"))

        assert (
            cli.main(["create-admin", "--email", "admin@example.org", "--password-stdin"]) == 1
        )
        assert "too_short" in capsys.readouterr().err

    def test_the_password_is_never_accepted_as_an_argument(self):
        """Command-line arguments end up in shell history and process listings."""
        with pytest.raises(SystemExit):
            cli.main(["create-admin", "--email", "a@example.org", "--password", "x"])

    @pytest.mark.parametrize("email", ["not-an-email", "admin@humani-t.local"])
    def test_an_email_the_api_would_refuse_is_refused_here_too(
        self, email: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """One rule for both doors: an admin created here must be able to sign in."""
        monkeypatch.setattr("sys.stdin", io.StringIO("violet-harbour-quietly-41-lanterns\n"))

        assert cli.main(["create-admin", "--email", email, "--password-stdin"]) == 1
        assert "email" in capsys.readouterr().err
