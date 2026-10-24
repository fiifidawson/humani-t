"""Operator commands: `uv run python -m app.cli <command>`.

These are the actions that must never be reachable over HTTP — creating key
material, re-encrypting it, checking the audit log, creating the first
administrator — so they run inside the
deployment, by someone with shell access to it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from email_validator import EmailNotValidError, validate_email

from app.api.dependencies import build_providers
from app.application.audit import SYSTEM
from app.crypto.local_key import generate_kek
from app.domain.accounts import EmailTakenError, PasswordRejectedError
from app.settings import get_settings


def _generate_kek(_: argparse.Namespace) -> int:
    # Printed, not stored: where it goes (a Docker secret, a password manager)
    # is the operator's decision, and the app must never keep a copy.
    print(generate_kek())
    return 0


async def _rewrap() -> int:
    providers = build_providers(get_settings())
    try:
        async with providers.storage.unit_of_work() as uow:
            moved = await providers.encryption.rewrap_all(uow)
            await providers.audit.record(
                uow,
                SYSTEM,
                "keys.rewrapped",
                "kek",
                str(providers.encryption.current_kek_version),
            )
            await uow.commit()
    finally:
        await providers.storage.close()
    print(f"rewrapped {moved} data key(s) under the current KEK")
    return 0


def _rewrap_keys(_: argparse.Namespace) -> int:
    return asyncio.run(_rewrap())


async def _verify() -> int:
    providers = build_providers(get_settings())
    try:
        async with providers.storage.unit_of_work() as uow:
            report = await providers.audit.verify(uow)
    finally:
        await providers.storage.close()
    if report.is_intact:
        print(f"audit chain intact: {report.events_checked} event(s)")
        return 0
    print(
        f"AUDIT CHAIN BROKEN at sequence {report.first_broken_sequence}: {report.reason}",
        file=sys.stderr,
    )
    return 1


def _verify_audit(_: argparse.Namespace) -> int:
    return asyncio.run(_verify())


async def _create_admin(email: str, password: str) -> int:
    providers = build_providers(get_settings())
    try:
        async with providers.storage.unit_of_work() as uow:
            user = await providers.accounts.create_user(
                uow,
                email=email,
                password=password,
                display_name=email.split("@")[0],
                is_admin=True,
            )
            await providers.audit.record(
                uow, SYSTEM, "account.admin_created", "user", str(user.id)
            )
            await uow.commit()
    except PasswordRejectedError as exc:
        print(f"password refused: {', '.join(exc.violations)}", file=sys.stderr)
        return 1
    except EmailTakenError:
        print("an account with this email already exists", file=sys.stderr)
        return 1
    finally:
        await providers.storage.close()
    print(f"created admin {user.email}")
    return 0


def _create_admin_command(args: argparse.Namespace) -> int:
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
    return asyncio.run(_create_admin(email, password))


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
    ).set_defaults(handler=_rewrap_keys)
    commands.add_parser(
        "verify-audit", help="Check the audit log's hash chain; exit 1 if it was tampered with."
    ).set_defaults(handler=_verify_audit)
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
    create_admin.set_defaults(handler=_create_admin_command)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
