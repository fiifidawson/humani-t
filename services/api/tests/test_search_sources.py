"""The real paper sources: OpenAlex, Europe PMC and arXiv.

Every response in `tests/fixtures/sources/` was recorded from the live API on
2026-11-03: the query in `QUERY` with three results per source,
`openalex_overlap.json` as a DOI lookup for the first Europe PMC result, and
`arxiv_error.xml` from `max_results=-1`. They are replayed through the fake
fetcher; no test in this file touches the network.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.domain.paper import PaperRecord, deduplicate
from app.domain.query import BooleanQuery, compile_vocabulary
from app.domain.sources import PaperSourceUnavailableError
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.net.base import FetchRequest, FetchResponse, FetchTimeoutError
from app.net.fake import FakeHttpFetcher
from app.net.pacing import Pacer
from app.search import arxiv, europepmc, openalex
from app.search.arxiv import ArxivProvider, compile_arxiv_query
from app.search.base import PaperSearchProvider
from app.search.europepmc import EuropePmcProvider, compile_europepmc_query
from app.search.openalex import OpenAlexProvider, compile_openalex_query

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sources"

QUERY = compile_vocabulary(
    Vocabulary(
        concepts=(
            Concept("chatbot", ("conversational agent",), theme="technology"),
            Concept("humanitarian", ("crisis",), theme="setting"),
        ),
        origin=VocabularyOrigin.USER_SUPPLIED,
    )
)

_PHRASE = re.compile(r'"([^"]*)"')

Handler = Callable[[FetchRequest], FetchResponse]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def replay(name: str, status: int = 200) -> Handler:
    content_type = "application/atom+xml" if name.endswith(".xml") else "application/json"

    def handler(request: FetchRequest) -> FetchResponse:
        return FetchResponse(
            status=status,
            url=request.url,
            headers={"content-type": content_type},
            body=fixture(name),
        )

    return handler


def failing(error: Exception) -> Handler:
    def handler(request: FetchRequest) -> FetchResponse:
        raise error

    return handler


def params_of(request: FetchRequest) -> dict[str, str]:
    return dict(request.params) | dict(parse_qsl(urlsplit(request.url).query))


def no_wait() -> Pacer:
    return Pacer(0)


def query_with(*concepts: Concept) -> BooleanQuery:
    return compile_vocabulary(
        Vocabulary(concepts=concepts, origin=VocabularyOrigin.USER_SUPPLIED)
    )


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


PROVIDERS = [
    pytest.param(
        lambda f: OpenAlexProvider(f, pacer=no_wait()),
        "openalex",
        "openalex_works.json",
        id="openalex",
    ),
    pytest.param(
        lambda f: EuropePmcProvider(f, pacer=no_wait()),
        "europepmc",
        "europepmc_search.json",
        id="europepmc",
    ),
    pytest.param(
        lambda f: ArxivProvider(f, pacer=no_wait()), "arxiv", "arxiv_query.xml", id="arxiv"
    ),
]


class TestEverySource:
    @pytest.mark.parametrize(("build", "source_id", "recording"), PROVIDERS)
    def test_it_satisfies_the_protocol_without_inheriting_it(self, build, source_id, recording):
        provider = build(FakeHttpFetcher())

        assert isinstance(provider, PaperSearchProvider)
        assert type(provider).__mro__[1:] == (object,)
        assert provider.source_id == source_id

    @pytest.mark.parametrize(("build", "source_id", "recording"), PROVIDERS)
    async def test_every_record_is_stamped_with_its_source_and_id(
        self, build, source_id, recording
    ):
        found = await build(FakeHttpFetcher(replay(recording))).search(QUERY)

        assert len(found) == 3
        assert all(r.source_id == source_id for r in found)
        assert all(r.external_id and r.url for r in found)

    @pytest.mark.parametrize(("build", "source_id", "recording"), PROVIDERS)
    async def test_an_error_status_is_reported_as_the_source_being_unavailable(
        self, build, source_id, recording
    ):
        provider = build(FakeHttpFetcher(replay(recording, status=503)))

        with pytest.raises(PaperSourceUnavailableError, match=source_id):
            await provider.search(QUERY)

    @pytest.mark.parametrize(("build", "source_id", "recording"), PROVIDERS)
    async def test_a_network_failure_is_reported_as_the_source_being_unavailable(
        self, build, source_id, recording
    ):
        provider = build(FakeHttpFetcher(failing(FetchTimeoutError("slow"))))

        with pytest.raises(PaperSourceUnavailableError) as raised:
            await provider.search(QUERY)

        assert isinstance(raised.value.__cause__, FetchTimeoutError)

    @pytest.mark.parametrize(("build", "source_id", "recording"), PROVIDERS)
    async def test_a_body_that_is_not_the_expected_format_is_unavailable_not_a_crash(
        self, build, source_id, recording
    ):
        def garbage(request: FetchRequest) -> FetchResponse:
            return FetchResponse(status=200, url=request.url, headers={}, body=b"<html>busy")

        with pytest.raises(PaperSourceUnavailableError):
            await build(FakeHttpFetcher(garbage)).search(QUERY)


class TestOpenAlex:
    def test_the_query_compiles_to_openalex_boolean_syntax(self):
        assert compile_openalex_query(QUERY) == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis")'
        )

    def test_a_strict_vocabulary_reaches_openalex_exactly_as_given(self):
        compiled = compile_openalex_query(QUERY)

        assert set(_PHRASE.findall(compiled)) == set(QUERY.all_terms)

    def test_a_quote_inside_a_term_cannot_break_out_of_its_phrase(self):
        query = query_with(Concept('foo" OR "bar'))

        assert compile_openalex_query(query) == '("foo  OR  bar")'

    async def test_search_sends_the_compiled_query_to_the_works_endpoint(self):
        fetcher = FakeHttpFetcher(replay("openalex_works.json"))

        await OpenAlexProvider(fetcher, pacer=no_wait()).search(QUERY, limit=3)

        (request,) = fetcher.requests
        params = params_of(request)
        assert urlsplit(request.url)._replace(query="").geturl() == (
            "https://api.openalex.org/works"
        )
        assert params["search.title_and_abstract"] == compile_openalex_query(QUERY)
        assert params["per_page"] == "3"
        assert params["page"] == "1"
        assert "mailto" not in params

    async def test_a_configured_mailto_is_sent(self):
        fetcher = FakeHttpFetcher(replay("openalex_works.json"))

        await OpenAlexProvider(fetcher, mailto="ops@example.org", pacer=no_wait()).search(QUERY)

        assert params_of(fetcher.requests[0])["mailto"] == "ops@example.org"

    async def test_recorded_works_map_to_paper_records(self):
        fetcher = FakeHttpFetcher(replay("openalex_works.json"))

        first, _, third = await OpenAlexProvider(fetcher, pacer=no_wait()).search(QUERY)

        assert first == PaperRecord(
            source_id="openalex",
            external_id="W1531824180",
            title="Embodied Conversational Agents for H5N1 Pandemic Crisis",
            year=2007,
            doi="10.20965/jaciii.2007.p0282",
            is_open_access=True,
            abstract=first.abstract,
            url="https://doi.org/10.20965/jaciii.2007.p0282",
        )
        assert (first.abstract or "").startswith(
            "This paper presents a novel framework for modeling embodied conversational"
        )
        assert third.doi is None
        assert third.url == "https://aisel.aisnet.org/amcis2026/conftheme/conftheme/3"

    async def test_a_limit_beyond_one_page_reads_further_pages(self):
        def pages(request: FetchRequest) -> FetchResponse:
            page = int(params_of(request)["page"])
            works = [
                {
                    "id": f"https://openalex.org/W{page}{i:03}",
                    "display_name": f"Paper {page}-{i}",
                }
                for i in range(100)
            ]
            return FetchResponse.of_json(
                request.url, {"meta": {"count": 250}, "results": works}
            )

        fetcher = FakeHttpFetcher(pages)

        found = await OpenAlexProvider(fetcher, pacer=no_wait()).search(QUERY, limit=150)

        assert len(found) == 150
        assert [params_of(r)["page"] for r in fetcher.requests] == ["1", "2"]
        assert {params_of(r)["per_page"] for r in fetcher.requests} == {"100"}

    async def test_a_short_page_ends_the_search(self):
        fetcher = FakeHttpFetcher(replay("openalex_works.json"))

        await OpenAlexProvider(fetcher, pacer=no_wait()).search(QUERY, limit=150)

        assert len(fetcher.requests) == 1

    async def test_a_work_without_a_title_is_skipped_rather_than_failing_the_search(self):
        def handler(request: FetchRequest) -> FetchResponse:
            works = [
                {"id": "https://openalex.org/W1", "display_name": None},
                {"id": "https://openalex.org/W2", "display_name": "Kept"},
            ]
            return FetchResponse.of_json(request.url, {"meta": {"count": 2}, "results": works})

        found = await OpenAlexProvider(FakeHttpFetcher(handler), pacer=no_wait()).search(QUERY)

        assert [r.external_id for r in found] == ["W2"]

    def test_pacing_is_at_least_one_second_without_an_api_key(self):
        """Searches with more than five Boolean operators: 1/s without a key."""
        assert openalex.pacer_for(has_api_key=False).interval_seconds >= 1.0
        assert openalex.pacer_for(has_api_key=True).interval_seconds >= 0.2


class TestEuropePmc:
    def test_the_query_compiles_to_europe_pmc_syntax_over_title_and_abstract(self):
        assert compile_europepmc_query(QUERY) == (
            '(TITLE_ABS:"chatbot" OR TITLE_ABS:"conversational agent") AND '
            '(TITLE_ABS:"humanitarian" OR TITLE_ABS:"crisis")'
        )

    def test_a_strict_vocabulary_reaches_europe_pmc_exactly_as_given(self):
        compiled = compile_europepmc_query(QUERY)

        assert set(_PHRASE.findall(compiled)) == set(QUERY.all_terms)

    async def test_search_asks_for_full_records_as_json(self):
        fetcher = FakeHttpFetcher(replay("europepmc_search.json"))

        await EuropePmcProvider(fetcher, pacer=no_wait()).search(QUERY, limit=3)

        (request,) = fetcher.requests
        params = params_of(request)
        assert urlsplit(request.url)._replace(query="").geturl() == (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        )
        assert params["query"] == compile_europepmc_query(QUERY)
        assert params["format"] == "json"
        assert params["resultType"] == "core"
        assert params["pageSize"] == "3"

    async def test_recorded_results_map_to_paper_records(self):
        fetcher = FakeHttpFetcher(replay("europepmc_search.json"))

        first, second, _ = await EuropePmcProvider(fetcher, pacer=no_wait()).search(QUERY)

        assert first.external_id == "MED:42846531"
        assert first.doi == "10.3389/fdgth.2026.1955671"
        assert first.year == 2026
        assert first.is_open_access is False
        assert first.url == "https://europepmc.org/article/MED/42846531"
        assert first.title.startswith("AI is already in the theatre of war")
        assert second.external_id == "PPR:PPR1352896"

    async def test_markup_in_abstracts_is_reduced_to_text(self):
        fetcher = FakeHttpFetcher(replay("europepmc_search.json"))

        _, second, _ = await EuropePmcProvider(fetcher, pacer=no_wait()).search(QUERY)

        assert second.abstract is not None
        assert "<" not in second.abstract
        assert second.abstract.startswith(
            "Abstract People increasingly disclose suicidal intent"
        )

    async def test_an_error_inside_a_200_response_is_reported(self):
        """Europe PMC reports some errors as 200 with errCode/errMsg in the body."""

        def handler(request: FetchRequest) -> FetchResponse:
            return FetchResponse.of_json(
                request.url, {"errCode": 404, "errMsg": "Invalid page size provided."}
            )

        with pytest.raises(PaperSourceUnavailableError, match="Invalid page size"):
            await EuropePmcProvider(FakeHttpFetcher(handler), pacer=no_wait()).search(QUERY)

    def test_pacing_stays_under_ten_requests_a_second(self):
        assert europepmc.pacer_for().interval_seconds >= 0.1


class TestArxiv:
    def test_the_query_compiles_to_arxiv_syntax_over_title_and_abstract(self):
        assert compile_arxiv_query(QUERY) == (
            '(ti:"chatbot" OR abs:"chatbot" OR ti:"conversational agent" OR '
            'abs:"conversational agent") AND (ti:"humanitarian" OR abs:"humanitarian" OR '
            'ti:"crisis" OR abs:"crisis")'
        )

    def test_a_strict_vocabulary_reaches_arxiv_exactly_as_given(self):
        compiled = compile_arxiv_query(QUERY)

        assert set(_PHRASE.findall(compiled)) == set(QUERY.all_terms)

    async def test_search_sends_the_compiled_query(self):
        fetcher = FakeHttpFetcher(replay("arxiv_query.xml"))

        await ArxivProvider(fetcher, pacer=no_wait()).search(QUERY, limit=3)

        (request,) = fetcher.requests
        params = params_of(request)
        assert urlsplit(request.url)._replace(query="").geturl() == (
            "https://export.arxiv.org/api/query"
        )
        assert params["search_query"] == compile_arxiv_query(QUERY)
        assert params["max_results"] == "3"
        assert params["start"] == "0"

    async def test_recorded_entries_map_to_paper_records(self):
        fetcher = FakeHttpFetcher(replay("arxiv_query.xml"))

        first, _, third = await ArxivProvider(fetcher, pacer=no_wait()).search(QUERY)

        assert first.external_id == "2610.39782v1"
        assert first.title.startswith("Assessing AI Companion Chatbot Safety")
        assert first.year == 2026
        assert first.is_open_access is True
        assert first.url == "https://arxiv.org/abs/2610.39782v1"
        assert first.doi is None
        assert (first.abstract or "").startswith("AI companion chatbots are increasingly used")
        assert third.doi == "10.1109/ICTAI62512.2024.00104"

    async def test_an_error_feed_is_reported_with_arxivs_reason(self):
        fetcher = FakeHttpFetcher(replay("arxiv_error.xml", status=400))

        with pytest.raises(
            PaperSourceUnavailableError, match="max_results must be non-negative"
        ):
            await ArxivProvider(fetcher, pacer=no_wait()).search(QUERY)

    async def test_a_document_type_declaration_is_refused_before_parsing(self):
        """Atom never needs a DTD; refusing one rules out entity-expansion attacks."""
        hostile = (
            b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>&b;</title></feed>'
        )

        def handler(request: FetchRequest) -> FetchResponse:
            return FetchResponse(status=200, url=request.url, headers={}, body=hostile)

        with pytest.raises(PaperSourceUnavailableError, match="DTD"):
            await ArxivProvider(FakeHttpFetcher(handler), pacer=no_wait()).search(QUERY)

    async def test_requests_are_spaced_at_least_three_seconds_apart(self):
        """arXiv terms of use: no more than one request every three seconds."""
        time_ = FakeTime()
        pacer = Pacer(arxiv.MIN_INTERVAL_SECONDS, clock=time_.clock, sleep=time_.sleep)
        provider = ArxivProvider(FakeHttpFetcher(replay("arxiv_query.xml")), pacer=pacer)

        await provider.search(QUERY)
        await provider.search(QUERY)

        assert arxiv.MIN_INTERVAL_SECONDS >= 3.0
        assert time_.sleeps == [pytest.approx(arxiv.MIN_INTERVAL_SECONDS)]


class TestAcrossSources:
    async def test_the_same_paper_from_two_sources_collapses_into_one(self):
        """OpenAlex reports DOIs as URLs, Europe PMC as bare DOIs; identity must agree."""
        from_openalex = await OpenAlexProvider(
            FakeHttpFetcher(replay("openalex_overlap.json")), pacer=no_wait()
        ).search(QUERY)
        from_epmc = await EuropePmcProvider(
            FakeHttpFetcher(replay("europepmc_search.json")), pacer=no_wait()
        ).search(QUERY)

        merged = deduplicate((*from_openalex, *from_epmc))

        assert len(from_openalex) == 1
        assert len(merged) == len(from_epmc)
        assert merged[0].source_id == "openalex"

    async def test_each_source_only_asks_its_own_host(self):
        hosts = {
            "openalex": ("api.openalex.org", OpenAlexProvider, "openalex_works.json"),
            "europepmc": ("www.ebi.ac.uk", EuropePmcProvider, "europepmc_search.json"),
            "arxiv": ("export.arxiv.org", ArxivProvider, "arxiv_query.xml"),
        }
        for host, provider_type, recording in hosts.values():
            fetcher = FakeHttpFetcher(replay(recording), allowed_hosts=(host,))

            await provider_type(fetcher, pacer=no_wait()).search(QUERY)

            assert {urlsplit(r.url).hostname for r in fetcher.requests} == {host}
