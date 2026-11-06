"""The SSRF-guarded fetcher, exercised for real down to the socket.

Only the socket layer is scripted (`tests/net_support.py`). Each test can see
which address a connection was opened to, which TLS server name it presented and
which bytes were sent, so "the request went where it was allowed to" is checked
on what actually happened rather than on what the code intended.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from app.net.base import (
    BlockedAddressError,
    Destination,
    DestinationNotAllowedError,
    FetchFailedError,
    FetchRequest,
    FetchResponse,
    FetchTimeoutError,
    HttpFetcher,
    OutboundPolicy,
    OutboundRefusedError,
    ResponseTooLargeError,
    TooManyRedirectsError,
)
from app.net.fake import FakeHttpFetcher, FakeResolver
from app.net.httpx_fetcher import HttpxFetcher, SystemResolver
from tests.net_support import (
    Drip,
    Hang,
    Refuse,
    ScriptedNetwork,
    http_response,
    json_response,
    redirect,
)
from tests.test_net_addresses import BLOCKED

HOST = "api.example.org"
PUBLIC_IP = "93.184.216.34"
OTHER_HOST = "cdn.example.org"
OTHER_IP = "93.184.216.35"


def make_fetcher(
    network: ScriptedNetwork,
    *destinations: Destination,
    resolver: FakeResolver | None = None,
    **limits: Any,
) -> HttpxFetcher:
    policy = OutboundPolicy(destinations=destinations or (Destination(HOST),), **limits)
    resolver = resolver or FakeResolver().answer(HOST, PUBLIC_IP).answer(OTHER_HOST, OTHER_IP)
    return HttpxFetcher(policy, resolver=resolver, network=network)


async def get(fetcher: HttpxFetcher, url: str = f"https://{HOST}/works") -> FetchResponse:
    return await fetcher.fetch(FetchRequest(url))


class TestConformance:
    def test_both_fetchers_satisfy_the_protocol_without_inheriting_it(self):
        real = make_fetcher(ScriptedNetwork())

        assert isinstance(real, HttpFetcher)
        assert isinstance(FakeHttpFetcher(), HttpFetcher)
        assert FakeHttpFetcher.__mro__[1:] == (object,)

    async def test_a_successful_fetch_returns_status_headers_and_body(self):
        fetcher = make_fetcher(ScriptedNetwork(json_response(b'{"ok": true}')))

        response = await get(fetcher)

        assert response.status == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"ok": True}
        assert response.url == f"https://{HOST}/works"


class TestAllowlist:
    async def test_a_host_off_the_allowlist_is_refused_before_any_lookup(self):
        network = ScriptedNetwork()
        resolver = FakeResolver().answer("evil.example.net", PUBLIC_IP)
        fetcher = make_fetcher(network, resolver=resolver)

        with pytest.raises(DestinationNotAllowedError):
            await get(fetcher, "https://evil.example.net/")

        assert resolver.calls == []
        assert network.connections == []

    async def test_a_subdomain_of_an_allowed_host_is_not_allowed(self):
        fetcher = make_fetcher(ScriptedNetwork())

        with pytest.raises(DestinationNotAllowedError):
            await get(fetcher, f"https://internal.{HOST}/")

    async def test_matching_ignores_case_and_a_trailing_dot(self):
        fetcher = make_fetcher(ScriptedNetwork(json_response(b"{}")))

        response = await get(fetcher, "https://API.Example.ORG./works")

        assert response.status == 200

    async def test_plain_http_is_refused_for_a_public_destination(self):
        network = ScriptedNetwork()

        with pytest.raises(DestinationNotAllowedError, match="https"):
            await get(make_fetcher(network), f"http://{HOST}/works")

        assert network.connections == []

    @pytest.mark.parametrize("url", ["ftp://api.example.org/x", "file:///etc/passwd"])
    async def test_schemes_other_than_http_are_refused(self, url):
        with pytest.raises(DestinationNotAllowedError):
            await get(make_fetcher(ScriptedNetwork()), url)

    async def test_credentials_embedded_in_the_url_are_refused(self):
        with pytest.raises(DestinationNotAllowedError):
            await get(make_fetcher(ScriptedNetwork()), f"https://user:pw@{HOST}/")

    async def test_an_operator_named_internal_host_may_use_http_and_a_private_address(
        self,
    ):
        network = ScriptedNetwork(json_response(b"{}"))
        internal = Destination("ollama", allows_private_addresses=True, allows_plain_http=True)
        fetcher = make_fetcher(
            network, internal, resolver=FakeResolver().answer("ollama", "172.18.0.5")
        )

        response = await get(fetcher, "http://ollama:11434/api/chat")

        assert response.status == 200
        assert (network.connections[0].host, network.connections[0].port) == (
            "172.18.0.5",
            11434,
        )


class TestBlockedAddresses:
    @pytest.mark.parametrize(("address", "why"), BLOCKED, ids=[a for a, _ in BLOCKED])
    async def test_an_allowed_name_that_resolves_to_a_blocked_address_is_refused(
        self, address, why
    ):
        network = ScriptedNetwork(json_response(b"{}"))
        fetcher = make_fetcher(network, resolver=FakeResolver().answer(HOST, address))

        with pytest.raises(BlockedAddressError):
            await get(fetcher)

        assert network.connections == [], why

    async def test_one_blocked_address_among_public_ones_refuses_the_whole_name(self):
        network = ScriptedNetwork(json_response(b"{}"))
        resolver = FakeResolver().answer(HOST, PUBLIC_IP, "10.0.0.7")

        with pytest.raises(BlockedAddressError):
            await get(make_fetcher(network, resolver=resolver))

        assert network.connections == []

    @pytest.mark.parametrize(
        "literal",
        [
            "127.0.0.1",
            "2130706433",
            "0177.0.0.1",
            "0x7f.1",
            "127.1",
            "[::ffff:127.0.0.1]",
            "[::1]",
            "169.254.169.254",
            "[fd00:ec2::254]",
        ],
    )
    async def test_an_address_literal_is_checked_even_when_an_operator_allowlists_it(
        self, literal
    ):
        """Decimal, octal, hex and v4-mapped spellings: never a lookup, never a connection."""
        network = ScriptedNetwork(json_response(b"{}"))
        resolver = FakeResolver()
        host = literal.strip("[]")
        fetcher = make_fetcher(network, Destination(host), resolver=resolver)

        with pytest.raises(OutboundRefusedError):
            await get(fetcher, f"https://{literal}/latest/meta-data/")

        assert resolver.calls == []
        assert network.connections == []

    async def test_a_name_that_does_not_resolve_is_a_failure_not_a_refusal(self):
        fetcher = make_fetcher(ScriptedNetwork(), resolver=FakeResolver())

        with pytest.raises(FetchFailedError):
            await get(fetcher)


class TestSystemResolver:
    """The operating system's resolver; `localhost` never leaves the machine."""

    async def test_localhost_resolves_to_loopback_addresses(self):
        addresses = await SystemResolver().resolve("localhost")

        assert addresses
        assert all(a.is_loopback for a in addresses)

    async def test_a_name_that_resolves_to_loopback_is_refused_by_the_real_resolver(self):
        network = ScriptedNetwork(json_response(b"{}"))
        fetcher = HttpxFetcher(
            OutboundPolicy(destinations=(Destination("localhost"),)), network=network
        )

        with pytest.raises(BlockedAddressError):
            await get(fetcher, "https://localhost/")

        assert network.connections == []


class TestDnsRebinding:
    async def test_the_connection_goes_to_the_address_that_was_checked(self):
        """The resolver would answer loopback next time; nothing ever asks it again."""
        network = ScriptedNetwork(json_response(b"{}"))
        resolver = FakeResolver().answer(HOST, PUBLIC_IP).answer(HOST, "127.0.0.1")

        await get(make_fetcher(network, resolver=resolver))

        assert resolver.calls == [HOST]
        assert [c.host for c in network.connections] == [PUBLIC_IP]

    async def test_the_host_header_and_tls_server_name_keep_the_hostname(self):
        network = ScriptedNetwork(json_response(b"{}"))

        await get(make_fetcher(network))

        connection = network.connections[0]
        assert connection.header("host") == HOST
        assert connection.sni == HOST

    async def test_a_rebound_answer_on_the_next_request_is_refused(self):
        network = ScriptedNetwork(json_response(b"{}"), json_response(b"{}"))
        resolver = FakeResolver().answer(HOST, PUBLIC_IP).answer(HOST, "127.0.0.1")
        fetcher = make_fetcher(network, resolver=resolver)

        await get(fetcher)
        with pytest.raises(BlockedAddressError):
            await get(fetcher)

        assert [c.host for c in network.connections] == [PUBLIC_IP]


class TestRedirects:
    async def test_a_redirect_is_followed_by_hand_and_each_hop_is_checked(self):
        network = ScriptedNetwork(
            redirect(f"https://{OTHER_HOST}/file"), json_response(b'{"hop": 2}')
        )
        fetcher = make_fetcher(network, Destination(HOST), Destination(OTHER_HOST))

        response = await get(fetcher)

        assert response.json() == {"hop": 2}
        assert response.url == f"https://{OTHER_HOST}/file"
        assert [c.host for c in network.connections] == [PUBLIC_IP, OTHER_IP]

    async def test_a_relative_location_resolves_against_the_current_url(self):
        network = ScriptedNetwork(redirect("/next?page=2"), json_response(b"{}"))

        response = await get(make_fetcher(network))

        assert response.url == f"https://{HOST}/next?page=2"

    async def test_a_redirect_to_an_allowed_name_on_an_internal_address_is_refused(self):
        network = ScriptedNetwork(redirect(f"https://{OTHER_HOST}/"))
        resolver = FakeResolver().answer(HOST, PUBLIC_IP).answer(OTHER_HOST, "10.0.0.5")
        fetcher = make_fetcher(
            network, Destination(HOST), Destination(OTHER_HOST), resolver=resolver
        )

        with pytest.raises(BlockedAddressError):
            await get(fetcher)

        assert len(network.connections) == 1

    @pytest.mark.parametrize(
        "location",
        [
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "https://169.254.169.254/latest/meta-data/",
            "https://[fd00:ec2::254]/latest/meta-data/",
            "https://localhost/admin",
            "https://evil.example.net/",
            f"http://{HOST}/downgraded",
        ],
    )
    async def test_a_redirect_off_the_allowlist_or_to_plain_http_is_refused(self, location):
        network = ScriptedNetwork(redirect(location))

        with pytest.raises(OutboundRefusedError):
            await get(make_fetcher(network))

        assert len(network.connections) == 1

    async def test_five_redirects_are_followed(self):
        network = ScriptedNetwork(*[redirect(f"/r{i}") for i in range(5)], json_response(b"{}"))

        response = await get(make_fetcher(network))

        assert response.url == f"https://{HOST}/r4"

    async def test_a_sixth_redirect_is_refused(self):
        network = ScriptedNetwork(*[redirect(f"/r{i}") for i in range(6)])

        with pytest.raises(TooManyRedirectsError):
            await get(make_fetcher(network))

        assert len(network.connections) == 6

    async def test_a_bearer_token_is_sent_only_to_the_destination_it_belongs_to(self):
        network = ScriptedNetwork(redirect(f"https://{OTHER_HOST}/"), json_response(b"{}"))
        fetcher = make_fetcher(
            network,
            Destination(HOST, bearer_token="s3cret-token"),
            Destination(OTHER_HOST),
        )

        await get(fetcher)

        first, second = network.connections
        assert first.header("authorization") == "Bearer s3cret-token"
        assert second.header("authorization") is None
        assert "s3cret-token" not in second.request_text

    async def test_see_other_turns_a_post_into_a_get_without_the_body(self):
        network = ScriptedNetwork(redirect("/result", status=303), json_response(b"{}"))

        await make_fetcher(network).fetch(
            FetchRequest.post_json(f"https://{HOST}/submit", {"secret": "payload"})
        )

        second = network.connections[1]
        assert second.request_text.startswith("GET /result ")
        assert "payload" not in second.request_text


class TestLimits:
    async def test_a_declared_oversized_body_is_refused_before_it_is_read(self):
        network = ScriptedNetwork(http_response(200, b"x" * 2000))

        with pytest.raises(ResponseTooLargeError):
            await get(make_fetcher(network, max_bytes=1000))

    async def test_an_undeclared_oversized_body_is_cut_off_at_the_cap(self):
        body = [b"x" * 600, b"x" * 600, b"x" * 600]
        network = ScriptedNetwork(
            [*http_response(200, declare_length=False), *body],
        )

        with pytest.raises(ResponseTooLargeError):
            await get(make_fetcher(network, max_bytes=1000))

    async def test_a_body_exactly_at_the_cap_is_accepted(self):
        network = ScriptedNetwork(http_response(200, b"x" * 1000))

        response = await get(make_fetcher(network, max_bytes=1000))

        assert len(response.body) == 1000

    async def test_a_silent_peer_times_out_on_the_read_timeout(self):
        network = ScriptedNetwork([*http_response(200, declare_length=False), Hang()])
        fetcher = make_fetcher(network, read_timeout_seconds=0.05)

        with pytest.raises(FetchTimeoutError):
            await get(fetcher)

        assert 0.05 in network.connections[0].read_timeouts

    async def test_a_slow_drip_is_stopped_by_the_total_deadline(self):
        """Each chunk arrives inside the read timeout; the deadline still ends it."""
        network = ScriptedNetwork([*http_response(200, declare_length=False), Drip(b"x", 0.01)])
        fetcher = make_fetcher(network, read_timeout_seconds=1, total_timeout_seconds=0.2)

        started = time.monotonic()
        with pytest.raises(FetchTimeoutError):
            await get(fetcher)

        assert time.monotonic() - started < 1

    async def test_a_refused_connection_is_reported_as_a_failure(self):
        with pytest.raises(FetchFailedError):
            await get(make_fetcher(ScriptedNetwork(Refuse())))


class TestRequests:
    @pytest.mark.parametrize(
        "header", ["Authorization", "authorization", "Cookie", "Proxy-Authorization", "Host"]
    )
    def test_callers_cannot_attach_credentials_or_a_host(self, header):
        with pytest.raises(ValueError, match="header"):
            FetchRequest(f"https://{HOST}/", headers=((header, "x"),))

    @pytest.mark.parametrize("method", ["PUT", "DELETE", "CONNECT", "get"])
    def test_only_get_and_post_are_supported(self, method):
        with pytest.raises(ValueError, match="method"):
            FetchRequest(f"https://{HOST}/", method=method)

    async def test_query_parameters_are_encoded_into_the_request_line(self):
        network = ScriptedNetwork(json_response(b"{}"))

        await make_fetcher(network).fetch(
            FetchRequest(f"https://{HOST}/works", params=(("q", '("a b" OR c)'),))
        )

        assert network.connections[0].request_text.startswith(
            "GET /works?q=%28%22a+b%22+OR+c%29 HTTP/1.1"
        )

    async def test_a_json_post_sends_the_serialised_body(self):
        network = ScriptedNetwork(json_response(b"{}"))

        await make_fetcher(network).fetch(
            FetchRequest.post_json(f"https://{HOST}/api", {"model": "m", "n": 1})
        )

        connection = network.connections[0]
        body = connection.request_text.split("\r\n\r\n", 1)[1]
        assert connection.header("content-type") == "application/json"
        assert json.loads(body) == {"model": "m", "n": 1}


class TestPolicy:
    @pytest.mark.parametrize(
        "host", ["", "https://api.example.org", "api.example.org:443", "api.example.org/x"]
    )
    def test_a_destination_is_a_bare_host_name(self, host):
        with pytest.raises(ValueError, match=r"Destination\.host"):
            Destination(host)

    def test_a_destination_never_shows_its_token(self):
        assert "s3cret" not in repr(Destination(HOST, bearer_token="s3cret"))

    def test_the_redirect_limit_cannot_be_raised_above_five(self):
        with pytest.raises(ValueError, match="max_redirects"):
            OutboundPolicy(destinations=(Destination(HOST),), max_redirects=6)

    @pytest.mark.parametrize(
        "limit",
        [
            "max_bytes",
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "total_timeout_seconds",
        ],
    )
    def test_limits_must_be_positive(self, limit):
        with pytest.raises(ValueError, match=limit):
            OutboundPolicy(destinations=(Destination(HOST),), **{limit: 0})


class TestFakeFetcher:
    async def test_it_records_requests_and_returns_the_scripted_response(self):
        fetcher = FakeHttpFetcher(lambda request: FetchResponse.of_json(request.url, {"n": 1}))

        response = await fetcher.fetch(FetchRequest(f"https://{HOST}/x"))

        assert response.json() == {"n": 1}
        assert [r.url for r in fetcher.requests] == [f"https://{HOST}/x"]

    async def test_it_refuses_hosts_off_its_allowlist_like_the_real_one(self):
        fetcher = FakeHttpFetcher(allowed_hosts=(HOST,))

        with pytest.raises(DestinationNotAllowedError):
            await fetcher.fetch(FetchRequest("https://evil.example.net/"))

        assert fetcher.requests == []
