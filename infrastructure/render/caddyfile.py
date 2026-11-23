"""Turn compose's Caddyfile into Render's: plain HTTP on $PORT, the API on localhost.

Render terminates HTTPS at its own proxy, so Caddy must not try to get certificates.
Each replacement must match exactly once: a silent miss would ship compose's
settings, so a changed Caddyfile fails the build instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Render's proxy connects from a private address; trusting private ranges lets
# Caddy pass the visitor's address on, so rate limits are per visitor.
_GLOBAL_OPTIONS = """\tadmin off
\tauto_https off
\tservers {
\t\ttrusted_proxies static private_ranges
\t\tclient_ip_headers X-Forwarded-For
\t}
"""

_REPLACEMENTS = (
    ("\tadmin off\n", _GLOBAL_OPTIONS),
    ("{$HUMANI_T_DOMAIN:localhost} {", ":{$PORT:10000} {"),
)


def render_caddyfile(source: str) -> str:
    """Return the Render variant of compose's Caddyfile."""
    result = source
    for old, new in _REPLACEMENTS:
        if result.count(old) != 1:
            raise SystemExit(f"Caddyfile changed: expected {old!r} exactly once")
        result = result.replace(old, new)
    if "reverse_proxy api:8000" not in result:
        raise SystemExit("Caddyfile changed: no reverse_proxy api:8000")
    return result.replace("reverse_proxy api:8000", "reverse_proxy 127.0.0.1:8000")


if __name__ == "__main__":
    source, target = sys.argv[1:]
    Path(target).write_text(render_caddyfile(Path(source).read_text()))
