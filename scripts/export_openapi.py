"""Write the API's OpenAPI document to a file, without starting a server.

The web app's types are generated from this file (`pnpm gen:api`), and CI checks
that the committed copy matches the code (`pnpm check:api`). It belongs here, not
in `app.cli`: it is a build step for developers, not an operator command.

Run it from the repository root:

    uv run --directory services/api python ../../scripts/export_openapi.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent.parent / "services" / "api"
_DEFAULT_OUTPUT = _API_DIR / "openapi.json"


def _forget_local_configuration() -> None:
    # The document must depend on the code alone. A developer's HUMANI_T_*
    # variables (a database URL without its KEK, say) would otherwise change
    # what is exported, or stop the export with a validation error.
    for name in [key for key in os.environ if key.upper().startswith("HUMANI_T_")]:
        del os.environ[name]


def export(output: Path) -> None:
    _forget_local_configuration()
    sys.path.insert(0, str(_API_DIR))

    from app.api.dependencies import build_providers
    from app.main import create_app
    from app.settings import Environment, Settings

    # The document is the production route surface: development-only routes
    # (`/info`) must not reach the web app's types. Production settings would
    # demand real secrets, so the providers come from a validated test
    # configuration and only the environment that decides which routes exist is
    # switched, unvalidated, on a copy. Nothing connects: the app is never served.
    test_settings = Settings(environment=Environment.TEST, public_origin="https://localhost")
    production_routes = test_settings.model_copy(update={"environment": Environment.PRODUCTION})
    app = create_app(production_routes, providers=build_providers(test_settings))
    document = app.openapi()
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    output.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    export(parser.parse_args().output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
