#!/usr/bin/env python3
"""Create the secret files the self-hosted stack (compose.yaml) reads.

Usage, from the repository root:
    python infrastructure/generate_secrets.py
    python infrastructure/generate_secrets.py --dir /etc/humani-t/secrets

Writes random passwords, a key-encryption key (KEK), and the connection URLs built
from those passwords, one value per file. Existing files are never overwritten,
so re-running it is safe, and the URLs always match passwords already in place
(including those scripts/dev_secrets.py created). Standard library only, so it runs
on any Python 3.9+ without installing anything.

Back the KEK up somewhere other than the database backups: see
docs/deploy/self-hosting.md, "Key custody".
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path
from urllib.parse import quote

_PASSWORDS = (
    "db_superuser_password",
    "db_migrator_password",
    "db_app_password",
    "valkey_password",
)
_DATABASE = "postgres:5432/humani_t"


def _write_once(directory: Path, name: str, value: str) -> str:
    path = directory / name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    # 0644 inside a 0700 directory: other users on the host cannot reach the file,
    # while the unprivileged users inside the containers can still read it.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
        file.write(value)
    print(f"created {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "secrets",
        help="Where to write the files. Set HUMANI_T_SECRETS_DIR to the same path.",
    )
    directory: Path = parser.parse_args().dir
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    passwords = {
        name: _write_once(directory, name, secrets.token_urlsafe(32)) for name in _PASSWORDS
    }
    _write_once(
        directory,
        "key_encryption_keys",
        f"1:{base64.b64encode(secrets.token_bytes(32)).decode()}",
    )

    def credential(name: str) -> str:
        return quote(passwords[name], safe="")

    _write_once(
        directory,
        "database_url",
        f"postgresql+asyncpg://humani_t_app:{credential('db_app_password')}@{_DATABASE}",
    )
    _write_once(
        directory,
        "migration_database_url",
        f"postgresql+asyncpg://humani_t_migrator:{credential('db_migrator_password')}"
        f"@{_DATABASE}",
    )
    _write_once(
        directory, "valkey_url", f"redis://:{credential('valkey_password')}@valkey:6379/0"
    )
    # Empty means "not configured". Fill in the ones your deployment needs: the mail
    # server's password, the bearer token of a model server on another machine
    # (docs/deploy/remote-model.md), an OpenAlex API key.
    for optional in ("smtp_password", "inference_token", "openalex_api_key"):
        _write_once(directory, optional, "")


if __name__ == "__main__":
    main()
