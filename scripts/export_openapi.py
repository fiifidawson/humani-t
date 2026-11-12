"""Write the API's OpenAPI document to a file, without starting a server.

The web app's types are generated from it, and CI checks the committed copy is current.
A developer build step, not an operator command, so it is not in `app.cli`.
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
    # The document must depend on the code alone, not on a developer's HUMANI_T_*
    # variables, which could change it or fail validation.
    for name in [key for key in os.environ if key.upper().startswith("HUMANI_T_")]:
        del os.environ[name]


def export(output: Path) -> None:
    _forget_local_configuration()
    sys.path.insert(0, str(_API_DIR))

    from app.api.dependencies import build_providers
    from app.main import create_app
    from app.settings import Environment, Settings

    # Production routes only, so `/info` stays out of the web app's types. Production
    # settings would demand real secrets, so test settings are copied with only the
    # environment switched. Nothing connects: the app is never served.
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
