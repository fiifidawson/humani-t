"""Which IP addresses outbound requests may reach, and how host literals are read.

Pure functions over addresses. Resolving names, and deciding which hosts are
allowed at all, happen elsewhere: this module only answers "is this address
somewhere a request from a paper's metadata must never land?".
"""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

_NAT64 = IPv6Network("64:ff9b::/96")
_HEX = re.compile(r"0[xX][0-9a-fA-F]+")
_OCTAL = re.compile(r"0[0-7]*")
_DECIMAL = re.compile(r"[1-9][0-9]*")
# Cloud metadata services live in these. Named explicitly rather than trusted to
# `is_global`, whose ranges have changed between Python releases.
_ALSO_BLOCKED: tuple[IPv4Network | IPv6Network, ...] = (
    IPv4Network("100.64.0.0/10"),  # carrier-grade NAT; Alibaba Cloud metadata lives here
    IPv4Network("192.0.0.0/24"),  # IETF protocol assignments; Oracle Cloud metadata
)


def is_blocked_address(address: IPv4Address | IPv6Address) -> bool:
    """True for anything but an ordinary public unicast address.

    Addresses that embed an IPv4 address (mapped, NAT64) are judged by the
    address they embed. 6to4 and Teredo are tunnels to wherever the embedded
    address points; no paper source uses them, so they are refused outright.
    """
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return is_blocked_address(address.ipv4_mapped)
        if address in _NAT64:
            return is_blocked_address(IPv4Address(int(address) & 0xFFFFFFFF))
        if address.sixtofour is not None or address.teredo is not None:
            return True
    return (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or any(address in network for network in _ALSO_BLOCKED)
    )


def parse_ip_literal(host: str) -> IPv4Address | IPv6Address | None:
    """Read `host` as an IP address if it is one, in any spelling a resolver accepts.

    Returns None for a name. IPv4 is read the way C's `inet_aton` reads it, so
    `2130706433`, `0177.0.0.1`, `0x7f.1` and `127.1` are all 127.0.0.1. Resolvers
    accept these forms, so a check that only understood dotted decimal would let
    them through.
    """
    candidate = host.strip().strip("[]")
    if ":" in candidate:
        try:
            return ip_address(candidate.split("%", 1)[0])
        except ValueError:
            return None
    return _parse_inet_aton(candidate)


def _parse_inet_aton(text: str) -> IPv4Address | None:
    parts = text.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = [_parse_inet_aton_number(part) for part in parts]
    if any(n is None for n in numbers):
        return None
    *leading, last = [n for n in numbers if n is not None]
    if any(n > 0xFF for n in leading):
        return None
    last_bits = 8 * (4 - len(leading))
    if last >= 1 << last_bits:
        return None
    value = last
    for index, number in enumerate(leading):
        value |= number << (8 * (3 - index))
    return IPv4Address(value)


def _parse_inet_aton_number(part: str) -> int | None:
    # Matched explicitly: int() would also accept "_", a sign and non-ASCII digits.
    if _HEX.fullmatch(part):
        return int(part[2:], 16)
    if _OCTAL.fullmatch(part):
        return int(part, 8)
    if _DECIMAL.fullmatch(part):
        return int(part, 10)
    return None
