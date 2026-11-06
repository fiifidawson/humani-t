"""Operator commands: `uv run python -m app.cli <command>`.

These are the actions that must never be reachable over HTTP — creating key
material, re-encrypting it, checking the audit log, creating the first
administrator — so they run inside the deployment, by someone with shell
access to it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable

from email_validator import EmailNotValidError, validate_email

from app.api.dependencies import Providers, build_providers
from app.application.audit import SYSTEM
from app.crypto.local_key import generate_kek
from app.domain.accounts import EmailTakenError, PasswordRejectedError
from app.settings import get_settings


def _run(command: Callable[[Providers], Awaitable[int]]) -> int:
    """Build the providers, run one command with them, and always close them."""

    async def run() -> int:
        providers = build_providers(get_settings())
        try:
            return await command(providers)
        finally:
            await providers.close()

    return asyncio.run(run())


def _generate_kek(_: argparse.Namespace) -> int:
    # Printed, not stored: where it goes (a Docker secret, a password manager)
    # is the operator's decision, and the app must never keep a copy.
    print(generate_kek())
    return 0


async def _rewrap(providers: Providers) -> int:
    async with providers.storage.unit_of_work() as uow:
        moved = await providers.encryption.rewrap_all(uow)
        await providers.audit.record(
            uow, SYSTEM, "keys.rewrapped", "kek", str(providers.encryption.current_kek_version)
        )
        await uow.commit()
    print(f"rewrapped {moved} data key(s) under the current KEK")
    return 0


async def _verify(providers: Providers) -> int:
    async with providers.storage.unit_of_work() as uow:
        report = await providers.audit.verify(uow)
    if report.is_intact:
        print(f"audit chain intact: {report.events_checked} event(s)")
        return 0
    print(
        f"AUDIT CHAIN BROKEN at sequence {report.first_broken_sequence}: {report.reason}",
        file=sys.stderr,
    )
    return 1


async def _purge(providers: Providers) -> int:
    report = await providers.accounts.purge_expired()
    print(
        f"purged {report.sessions} session(s), {report.password_resets} reset link(s), "
        f"{report.invitations} invitation(s), "
        f"{report.webauthn_challenges} passkey challenge(s)"
    )
    return 0


def _create_admin(args: argparse.Namespace) -> int:
    # The password comes on stdin only: arguments end up in shell history and
    # in process listings that other users of the machine can read.
    password = sys.stdin.readline().rstrip("\r\n")
    # The same validator, with the same settings, as the API's EmailStr: an
    # address accepted here must be one the admin can sign in with.
    try:
        email = validate_email(args.email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        print(f"email refused: {exc}", file=sys.stderr)
        return 1

    async def create(providers: Providers) -> int:
        try:
            user = await providers.accounts.create_user(
                email=email, password=password, display_name=email.split("@")[0], is_admin=True
            )
        except PasswordRejectedError as exc:
            print(f"password refused: {', '.join(exc.violations)}", file=sys.stderr)
            return 1
        except EmailTakenError:
            print("an account with this email already exists", file=sys.stderr)
            return 1
        print(f"created admin {user.email}")
        return 0

    return _run(create)


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev=False: otherwise `--password` would silently match `--password-stdin`.
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "generate-kek", help="Print a new key-encryption key (base64, 32 bytes)."
    ).set_defaults(handler=_generate_kek)
    commands.add_parser(
        "rewrap-keys", help="Re-encrypt every data key under the newest KEK version."
    ).set_defaults(handler=lambda _: _run(_rewrap))
    commands.add_parser(
        "verify-audit", help="Check the audit log's hash chain; exit 1 if it was tampered with."
    ).set_defaults(handler=lambda _: _run(_verify))
    commands.add_parser(
        "purge-expired", help="Delete ended sessions and spent links. Safe to run any time."
    ).set_defaults(handler=lambda _: _run(_purge))
    create_admin = commands.add_parser(
        "create-admin",
        help="Create an administrator. Pipe the password in: echo … | … --password-stdin",
        allow_abbrev=False,
    )
    create_admin.add_argument("--email", required=True)
    create_admin.add_argument(
        "--password-stdin",
        action="store_true",
        required=True,
        help="Read the password from standard input (the only way to supply it).",
    )
    create_admin.set_defaults(handler=_create_admin)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
