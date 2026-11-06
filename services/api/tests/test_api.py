"""The HTTP boundary: compilation, search, and the provider switch.

Compiling and listing sources need a signed-in user; searching happens inside a
project (M10). The `member` fixture is that user, in a browser that sends its
CSRF token on every request as the web app does.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_search_provider
from app.api.routes.search import SOURCE_DESCRIPTIONS
from app.domain.paper import PaperRecord
from app.main import app
from app.search.fake import FakePaperSearchProvider
from app.settings import PAPER_SOURCE_IDS
from tests.project_support import (
    OWNER_EMAIL,
    PASSWORD,
    create_project,
    make_project_harness,
    signed_in,
)
from tests.support import AuthHarness, csrf_headers

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


@pytest.fixture
def harness() -> AuthHarness:
    harness = make_project_harness()
    harness.create_user(OWNER_EMAIL, PASSWORD)
    return harness


@pytest.fixture
def member(harness: AuthHarness) -> TestClient:
    browser = signed_in(harness, OWNER_EMAIL)
    browser.headers.update(csrf_headers(browser))
    return browser


@pytest.fixture
def search_url(member: TestClient) -> str:
    return f"/api/v1/projects/{create_project(member)['id']}/search"


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

    def test_sources_lists_what_is_reachable(self, member):
        body = member.get("/api/v1/sources").json()

        assert [s["source_id"] for s in body] == ["fake"]
        assert body[0]["live"] is False


class TestSourceDescriptions:
    """What the sources list tells a reviewer matches docs/dependencies.md §4."""

    def test_every_source_that_can_be_configured_is_described(self):
        assert set(SOURCE_DESCRIPTIONS) == {"fake", *PAPER_SOURCE_IDS}

    def test_google_scholar_and_the_retired_openalex_polite_pool_are_not_mentioned(self):
        described = " ".join(SOURCE_DESCRIPTIONS.values()).lower()

        for retired in ("scholar", "mailto", "polite"):
            assert retired not in described

    def test_openalex_says_an_api_key_raises_its_limits(self):
        assert "api key" in SOURCE_DESCRIPTIONS["openalex"].lower()


class TestCompile:
    def test_renders_or_within_theme_and_and_across_themes(self, member):
        body = member.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        assert body["rendered"] == (
            '("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")'
        )
        assert body["term_count"] == 4

    def test_provenance_is_returned(self, member):
        body = member.post(
            "/api/v1/vocabulary/compile",
            json={**VOCABULARY, "origin": "user_supplied", "version": 3},
        ).json()

        assert body["provenance"] == {
            "vocabulary_version": 3,
            "vocabulary_origin": "user_supplied",
            "is_strict": True,
        }

    def test_structure_groups_terms_under_their_concept(self, member):
        body = member.post("/api/v1/vocabulary/compile", json=VOCABULARY).json()

        technology = body["themes"][0]
        assert technology["theme"] == "technology"
        assert technology["groups"][0] == {
            "concept": "chatbot",
            "terms": ["chatbot", "conversational agent"],
        }

    def test_empty_concepts_is_rejected(self, member):
        response = member.post(
            "/api/v1/vocabulary/compile", json={"concepts": [], "origin": "llm_assisted"}
        )
        assert response.status_code == 422

    def test_blank_term_is_rejected(self, member):
        response = member.post(
            "/api/v1/vocabulary/compile",
            json={"concepts": [{"term": "   "}], "origin": "llm_assisted"},
        )
        assert response.status_code == 422


class TestSearch:
    def test_returns_results_with_the_query_that_produced_them(self, member, search_url):
        body = member.post(search_url, json={**VOCABULARY, "limit": 25}).json()

        assert body["source_id"] == "fake"
        assert body["total"] == 2
        assert "chatbot" in body["query"]["rendered"]
        assert body["query"]["provenance"]["is_strict"] is False

    def test_and_across_themes_excludes_single_theme_matches(self, member, search_url):
        body = member.post(search_url, json=VOCABULARY).json()
        ids = {r["external_id"] for r in body["results"]}

        assert ids == {"fake-001", "fake-002"}

    def test_every_result_carries_a_dedup_identity(self, member, search_url):
        body = member.post(search_url, json=VOCABULARY).json()

        assert all(r["identity"].startswith("doi:") for r in body["results"])

    def test_limit_is_respected(self, member, search_url):
        body = member.post(search_url, json={**VOCABULARY, "limit": 1}).json()
        assert body["total"] == 1

    def test_limit_is_bounded(self, member, search_url):
        assert member.post(search_url, json={**VOCABULARY, "limit": 0}).status_code == 422
        assert member.post(search_url, json={**VOCABULARY, "limit": 999}).status_code == 422

    def test_no_match_returns_empty_not_error(self, member, search_url):
        body = member.post(
            search_url,
            json={"concepts": [{"term": "quantum chromodynamics"}]},
        ).json()

        assert body["total"] == 0
        assert body["results"] == []


class TestProviderSwap:
    """The claim the architecture makes, tested.

    Overriding the provider changes what the endpoint returns without touching
    the route, the service, the schemas or the domain.
    """

    def test_swapping_the_provider_changes_only_the_data(self, harness, member, search_url):
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
        harness.app.dependency_overrides[get_search_provider] = lambda: custom
        try:
            body = member.post(search_url, json=VOCABULARY).json()
        finally:
            harness.app.dependency_overrides.clear()

        assert [r["external_id"] for r in body["results"]] == ["swap-1"]

    def test_original_provider_is_restored_after_the_override(
        self, harness, member, search_url
    ):
        body = member.post(search_url, json=VOCABULARY).json()
        assert {r["external_id"] for r in body["results"]} == {"fake-001", "fake-002"}


class TestOpenAPI:
    def test_all_endpoints_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert set(paths) == {
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/info",
            "/api/v1/sources",
            "/api/v1/vocabulary/compile",
            "/api/v1/projects",
            "/api/v1/projects/{project_id}",
            "/api/v1/projects/{project_id}/archive",
            "/api/v1/projects/{project_id}/unarchive",
            "/api/v1/projects/{project_id}/members",
            "/api/v1/projects/{project_id}/members/{user_id}",
            "/api/v1/projects/{project_id}/search",
            "/api/v1/projects/{project_id}/reviews",
            "/api/v1/projects/{project_id}/reviews/{review_id}",
            "/api/v1/reviews/{review_id}/runs",
            "/api/v1/runs/{run_id}/events",
            "/api/v1/runs/{run_id}/cancel",
            "/api/v1/runs/{run_id}/manifest",
            "/api/v1/reviews/{review_id}/papers",
            "/api/v1/reviews/{review_id}/papers/{paper_id}",
            "/api/v1/reviews/{review_id}/prisma",
            "/api/v1/gates/{gate_id}",
            "/api/v1/reviews/{review_id}/brief",
            "/api/v1/briefs/{brief_id}/evidence/{tag}",
            "/api/v1/briefs/{brief_id}/trace",
            "/api/v1/reviews/{review_id}/exports/{name}",
            "/api/v1/reviews/{review_id}/questions",
            "/api/v1/reviews/{review_id}/threads",
            "/api/v1/threads/{thread_id}",
            "/api/v1/questions/{question_id}",
            "/api/v1/projects/{project_id}/masking-rules",
            "/api/v1/projects/{project_id}/masking-rules/{rule_id}",
            "/api/v1/projects/{project_id}/documents",
            "/api/v1/projects/{project_id}/documents/{document_id}",
            "/api/v1/projects/{project_id}/documents/{document_id}/masked",
            "/api/v1/projects/{project_id}/documents/{document_id}/review",
            "/api/v1/projects/{project_id}/documents/{document_id}/masks",
            "/api/v1/projects/{project_id}/documents/{document_id}/unmask",
            "/api/v1/projects/{project_id}/documents/{document_id}/approve",
            "/api/v1/projects/{project_id}/documents/{document_id}/reject",
            "/api/v1/reviews/{review_id}/plan",
            "/api/v1/reviews/{review_id}/plan/answers",
            "/api/v1/reviews/{review_id}/plan/changes",
            "/api/v1/reviews/{review_id}/plan/accept",
            "/api/v1/reviews/{review_id}/plan/vocabulary",
            "/api/v1/reviews/{review_id}/plan/versions/{version}",
            "/api/v1/auth/login",
            "/api/v1/auth/logout",
            "/api/v1/auth/session",
            "/api/v1/auth/reauthenticate",
            "/api/v1/auth/register",
            "/api/v1/auth/password-reset",
            "/api/v1/auth/password-reset/confirm",
            "/api/v1/account/sessions",
            "/api/v1/account/sessions/{session_id}",
            "/api/v1/account/sessions/revoke-others",
            "/api/v1/account/password",
            "/api/v1/auth/mfa/totp",
            "/api/v1/auth/mfa/recovery-code",
            "/api/v1/auth/mfa/passkey/options",
            "/api/v1/auth/mfa/passkey",
            "/api/v1/account/mfa",
            "/api/v1/account/mfa/totp",
            "/api/v1/account/mfa/totp/confirm",
            "/api/v1/account/mfa/passkeys/options",
            "/api/v1/account/mfa/passkeys",
            "/api/v1/account/mfa/passkeys/{passkey_id}",
            "/api/v1/account/mfa/recovery-codes",
            "/api/v1/admin/invitations",
            "/api/v1/admin/users/{user_id}/disable",
            "/api/v1/admin/users/{user_id}/enable",
        }

    def test_request_bodies_carry_a_worked_example(self, client):
        """Swagger's 'Try it out' should prefill something that actually works."""
        schema = client.get("/openapi.json").json()
        example = schema["components"]["schemas"]["VocabularyIn"]["example"]

        assert example["concepts"][0]["term"] == "chatbot"

    def test_endpoints_are_grouped_by_tag(self, client):
        schema = client.get("/openapi.json").json()
        tags = {t["name"] for t in schema["tags"]}

        assert tags == {
            "system",
            "auth",
            "account",
            "admin",
            "projects",
            "reviews",
            "runs",
            "briefs",
            "exports",
            "questions",
            "plans",
            "papers",
            "masking",
            "documents",
            "vocabulary",
            "search",
        }
