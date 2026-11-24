"""The repository has one version (docs/standards.md, Releases).

Every place that states it must agree, and the changelog must describe it, so a tag
never points at a commit whose API, web app and README disagree about what it is.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from app.api.routes.system import API_VERSION

_REPO = Path(__file__).resolve().parents[3]
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _version() -> str:
    pyproject = tomllib.loads((_REPO / "services/api/pyproject.toml").read_text("utf-8"))
    version: str = pyproject["project"]["version"]
    return version


def _package_version(path: str) -> str:
    version: str = json.loads((_REPO / path).read_text("utf-8"))["version"]
    return version


class TestOneVersion:
    def test_the_version_is_semantic(self):
        assert _SEMVER.match(_version())

    def test_the_api_reports_the_repository_version(self):
        assert _version() == API_VERSION

    def test_both_package_files_carry_the_repository_version(self):
        assert _package_version("package.json") == _version()
        assert _package_version("apps/web/package.json") == _version()

    def test_the_readme_badge_shows_the_repository_version(self):
        readme = (_REPO / "README.md").read_text("utf-8")

        assert f"img.shields.io/badge/version-{_version()}-" in readme

    def test_the_changelog_describes_the_repository_version(self):
        changelog = (_REPO / "CHANGELOG.md").read_text("utf-8")

        assert re.search(
            rf"^## \[{re.escape(_version())}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M
        )
