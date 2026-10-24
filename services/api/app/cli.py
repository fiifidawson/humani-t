"""Operator commands: `uv run python -m app.cli <command>`.

These are the actions that must never be reachable over HTTP — creating key
material and re-encrypting it — so they run inside the deployment, by someone
with shell access to it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.api.dependencies import build_providers
from app.crypto.local_key import generate_kek
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
            await uow.commit()
    finally:
        await providers.storage.close()
    print(f"rewrapped {moved} data key(s) under the current KEK")
    return 0


def _rewrap_keys(_: argparse.Namespace) -> int:
    return asyncio.run(_rewrap())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "generate-kek", help="Print a new key-encryption key (base64, 32 bytes)."
    ).set_defaults(handler=_generate_kek)
    commands.add_parser(
        "rewrap-keys", help="Re-encrypt every data key under the newest KEK version."
    ).set_defaults(handler=_rewrap_keys)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
