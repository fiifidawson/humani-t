"""The HTTP boundary: compilation, search, and the provider switch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_search_provider
from app.domain.paper import PaperRecord
from app.main import app
from app.search.fake import FakePaperSearchProvider

VOCABULARY = {
    "origin": "llm_assisted",
    "concepts": [
        {"term": "chatbot", "synonyms": ["conversational agent"], "theme": "technology"},
        {"term": "humanitarian", "synonyms": ["crisis response"], "theme": "setting"},
    ],
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSystem:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_info_reports_the_active_provider(self, client):
        body = client.get("/api/v1/info").json()

        assert body["search_provider"] == "fake"
        assert body["makes_network_calls"] is False
        assert body["api_prefix"] == "/api/v1"

    def test_sources_lists_what_is_reachable(self, client):
        body = client.get("/api/v1/sources").json()

        assert [s["source_id"] for s in body] == ["fake"]
        assert body[0]["live"] is False


class TestCompile:
    def test_renders_or_within_theme_and_and_across_themes(self, client):
        body = client.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        assert body["rendered"] == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")'
        )
        assert body["term_count"] == 4

    def test_provenance_is_returned(self, client):
        body = client.post(
            "/api/v1/vocabulary/compile",
            json={**VOCABULARY, "origin": "user_supplied", "version": 3},
        ).json()

        assert body["provenance"] == {
            "vocabulary_version": 3,
            "vocabulary_origin": "user_supplied",
            "is_strict": True,
        }

    def test_structure_groups_terms_under_their_concept(self, client):
        body = client.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        technology = body["themes"][0]
        assert technology["theme"] == "technology"
        assert technology["groups"][0] == {
            "concept": "chatbot",
            "terms": ["chatbot", "conversational agent"],
        }

    def test_empty_concepts_is_rejected(self, client):
        response = client.post(
            "/api/v1/vocabulary/compile", json={"concepts": [], "origin": "llm_assisted"}
        )
        assert response.status_code == 422

    def test_blank_term_is_rejected(self, client):
        response = client.post(
            "/api/v1/vocabulary/compile",
            json={"concepts": [{"term": "   "}], "origin": "llm_assisted"},
        )
        assert response.status_code == 422


class TestSearch:
    def test_returns_results_with_the_query_that_produced_them(self, client):
        body = client.post("/api/v1/search", json={**VOCABULARY, "limit": 25}).json()

        assert body["source_id"] == "fake"
        assert body["total"] == 2
        assert "chatbot" in body["query"]["rendered"]
        assert body["query"]["provenance"]["is_strict"] is False

    def test_and_across_themes_excludes_single_theme_matches(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()
        ids = {r["external_id"] for r in body["results"]}

        assert ids == {"fake-001", "fake-002"}

    def test_every_result_carries_a_dedup_identity(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()

        assert all(r["identity"].startswith("doi:") for r in body["results"])

    def test_limit_is_respected(self, client):
        body = client.post("/api/v1/search", json={**VOCABULARY, "limit": 1}).json()
        assert body["total"] == 1

    def test_limit_is_bounded(self, client):
        assert client.post("/api/v1/search", json={**VOCABULARY, "limit": 0}).status_code == 422
        assert (
            client.post("/api/v1/search", json={**VOCABULARY, "limit": 999}).status_code == 422
        )

    def test_no_match_returns_empty_not_error(self, client):
        body = client.post(
            "/api/v1/search",
            json={"concepts": [{"term": "quantum chromodynamics"}]},
        ).json()

        assert body["total"] == 0
        assert body["results"] == []


class TestProviderSwap:
    """The claim the architecture makes, tested.

    Overriding the provider changes what the endpoint returns without touching
    the route, the service, the schemas or the domain.
    """

    def test_swapping_the_provider_changes_only_the_data(self, client):
        custom = FakePaperSearchProvider(
            corpus=(
                PaperRecord(
                    source_id="fake",
                    external_id="swap-1",
                    title="Chatbot ethics in humanitarian triage",
                    year=2025,
                    doi="10.1/swap",
                ),
            )
        )
        app.dependency_overrides[get_search_provider] = lambda: custom
        try:
            body = client.post("/api/v1/search", json=VOCABULARY).json()
        finally:
            app.dependency_overrides.clear()

        assert [r["external_id"] for r in body["results"]] == ["swap-1"]

    def test_original_provider_is_restored_after_the_override(self, client):
        body = client.post("/api/v1/search", json=VOCABULARY).json()
        assert {r["external_id"] for r in body["results"]} == {"fake-001", "fake-002"}


class TestOpenAPI:
    def test_all_endpoints_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert set(paths) == {
            "/api/v1/health",
            "/api/v1/info",
            "/api/v1/sources",
            "/api/v1/vocabulary/compile",
            "/api/v1/search",
        }

    def test_request_bodies_carry_a_worked_example(self, client):
        """Swagger's 'Try it out' should prefill something that actually works."""
        schema = client.get("/openapi.json").json()
        example = schema["components"]["schemas"]["VocabularyIn"]["example"]

        assert example["concepts"][0]["term"] == "chatbot"

    def test_endpoints_are_grouped_by_tag(self, client):
        schema = client.get("/openapi.json").json()
        tags = {t["name"] for t in schema["tags"]}

        assert tags == {"system", "vocabulary", "search"}
