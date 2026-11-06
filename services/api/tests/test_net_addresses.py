"""Which addresses an outbound request may reach, decided on the address itself.

A hostname proves nothing: DNS can point any name at any address. These tests
pin the address rules that `HttpxFetcher` applies at connect time.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

import pytest

from app.net.addresses import is_blocked_address, parse_ip_literal

BLOCKED = [
    ("127.0.0.1", "loopback"),
    ("127.255.255.254", "loopback, top of the range"),
    ("10.0.0.1", "private"),
    ("172.16.0.1", "private"),
    ("172.31.255.255", "private, top of the range"),
    ("192.168.1.1", "private"),
    ("169.254.169.254", "cloud metadata (AWS, GCP, Azure)"),
    ("169.254.0.1", "link-local"),
    ("100.64.0.1", "carrier-grade NAT"),
    ("100.100.100.200", "cloud metadata (Alibaba), inside CGNAT"),
    ("0.0.0.0", "unspecified"),  # noqa: S104 — an address under test, not a bind
    ("0.1.2.3", "this network"),
    ("224.0.0.1", "multicast"),
    ("224.0.1.1", "multicast that Python calls global"),
    ("239.255.255.250", "multicast (SSDP)"),
    ("240.0.0.1", "reserved"),
    ("255.255.255.255", "broadcast"),
    ("192.0.0.192", "IETF protocol assignments (Oracle Cloud metadata)"),
    ("192.0.2.1", "documentation"),
    ("198.18.0.1", "benchmarking"),
    ("::1", "IPv6 loopback"),
    ("::", "IPv6 unspecified"),
    ("fe80::1", "IPv6 link-local"),
    ("fc00::1", "IPv6 unique local"),
    ("fd00:ec2::254", "cloud metadata (AWS IMDS over IPv6)"),
    ("ff02::1", "IPv6 multicast"),
    ("2001:db8::1", "IPv6 documentation"),
    ("100::1", "IPv6 discard-only"),
    ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ("::ffff:169.254.169.254", "IPv4-mapped metadata"),
    ("::ffff:10.0.0.1", "IPv4-mapped private"),
    ("::127.0.0.1", "IPv4-compatible (deprecated) loopback"),
    ("64:ff9b::7f00:1", "NAT64 of loopback"),
    ("64:ff9b::a9fe:a9fe", "NAT64 of metadata"),
    ("2002:7f00:1::", "6to4 embedding loopback"),
    ("2002:808:808::", "6to4 even when the embedded address is public"),
    ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "Teredo"),
]

ALLOWED = [
    ("93.184.216.34", "public IPv4"),
    ("8.8.8.8", "public IPv4"),
    ("2606:4700:4700::1111", "public IPv6"),
    ("::ffff:8.8.8.8", "IPv4-mapped public address"),
    ("64:ff9b::808:808", "NAT64 of a public address (DNS64 on IPv6-only hosts)"),
]


class TestBlockedAddresses:
    @pytest.mark.parametrize(("address", "why"), BLOCKED, ids=[a for a, _ in BLOCKED])
    def test_internal_and_special_addresses_are_blocked(self, address, why):
        assert is_blocked_address(ip_address(address)), why

    @pytest.mark.parametrize(("address", "why"), ALLOWED, ids=[a for a, _ in ALLOWED])
    def test_public_addresses_are_allowed(self, address, why):
        assert not is_blocked_address(ip_address(address)), why


class TestHostLiterals:
    """Every spelling that the C library's `inet_aton` would turn into an address.

    URL parsers disagree on these forms. Interpreting them ourselves means a
    host that a resolver would treat as 127.0.0.1 is checked as 127.0.0.1.
    """

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("127.0.0.1", "127.0.0.1"),
            ("2130706433", "127.0.0.1"),
            ("0177.0.0.1", "127.0.0.1"),
            ("017700000001", "127.0.0.1"),
            ("0x7f.0.0.1", "127.0.0.1"),
            ("0x7f000001", "127.0.0.1"),
            ("0X7F.1", "127.0.0.1"),
            ("127.1", "127.0.0.1"),
            ("10.1.2", "10.1.0.2"),
            ("169.254.43518", "169.254.169.254"),
            ("0251.0376.0251.0376", "169.254.169.254"),
            ("0xa9.0xfe.0xa9.0xfe", "169.254.169.254"),
        ],
    )
    def test_ipv4_spellings_are_read_as_the_address_they_mean(self, host, expected):
        assert parse_ip_literal(host) == IPv4Address(expected)

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("::1", "::1"),
            ("[::1]", "::1"),
            ("::ffff:127.0.0.1", "::ffff:7f00:1"),
            ("[fd00:ec2::254]", "fd00:ec2::254"),
            ("fe80::1%eth0", "fe80::1"),
        ],
    )
    def test_ipv6_literals_are_read_with_or_without_brackets(self, host, expected):
        assert parse_ip_literal(host) == IPv6Address(expected)

    @pytest.mark.parametrize(
        "host",
        [
            "api.openalex.org",
            "localhost",
            "ollama",
            "1.2.3.4.5",
            "256.0.0.1",
            "1.2.3.256x",
            "08.0.0.1",
            "0x",
            "4294967296",
            "1.16777216",
            "",
        ],
    )
    def test_names_and_malformed_numbers_are_not_addresses(self, host):
        assert parse_ip_literal(host) is None
