"""Requirements for collections, tags and the project list's filters, as pure domain rules.

A collection name is shared across the deployment, so two spellings of one name are one
name. Tags belong to a project. Filters and sort orders are what the All projects page
asks for (UI_UX_SPEC §7.2); they only ever narrow the caller's own projects.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.collections import (
    MAX_COLLECTION_NAME_LENGTH,
    MAX_TAG_LENGTH,
    MAX_TAGS_PER_PROJECT,
    Collection,
    collection_name_key,
    may_manage_collection,
    normalize_collection_name,
    normalize_tag,
    normalize_tags,
)
from app.domain.project_list import ProjectFacts, ProjectListQuery, ProjectSort
from app.domain.projects import ProjectStatus

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
ME, OTHER = uuid.uuid4(), uuid.uuid4()


class TestCollectionNames:
    def test_a_name_is_trimmed(self):
        assert normalize_collection_name("  Sahel 2026 ") == "Sahel 2026"

    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_blank_name_is_refused(self, name):
        with pytest.raises(ValueError, match=r"Collection\.name"):
            normalize_collection_name(name)

    def test_a_name_over_the_limit_is_refused(self):
        with pytest.raises(ValueError, match="at most"):
            normalize_collection_name("x" * (MAX_COLLECTION_NAME_LENGTH + 1))

    @pytest.mark.parametrize("name", ["Sahel\n2026", "Sahel‮2026"])
    def test_a_name_that_hides_its_own_text_is_refused(self, name):
        with pytest.raises(ValueError, match="control characters"):
            normalize_collection_name(name)

    def test_names_differing_only_in_case_and_spacing_are_the_same_name(self):
        assert collection_name_key("Sahel  2026") == collection_name_key(" sahel 2026")

    def test_different_names_have_different_keys(self):
        assert collection_name_key("Sahel 2026") != collection_name_key("Sahel 2025")


class TestCollectionRecord:
    def test_a_collection_needs_its_sealed_name(self):
        with pytest.raises(ValueError, match="name_sealed"):
            Collection(id=uuid.uuid4(), name_sealed=b"", created_by=ME, created_at=NOW)

    def test_its_creator_may_manage_it(self):
        collection = Collection(uuid.uuid4(), b"sealed", ME, NOW)

        assert may_manage_collection(collection, user_id=ME, is_admin=False)

    def test_an_administrator_may_manage_it(self):
        collection = Collection(uuid.uuid4(), b"sealed", OTHER, NOW)

        assert may_manage_collection(collection, user_id=ME, is_admin=True)

    def test_anyone_else_may_not(self):
        collection = Collection(uuid.uuid4(), b"sealed", OTHER, NOW)

        assert not may_manage_collection(collection, user_id=ME, is_admin=False)


class TestTags:
    def test_tags_are_trimmed_and_inner_spaces_collapsed(self):
        assert normalize_tags(["  Mali ", "cash   transfers"]) == ("Mali", "cash transfers")

    def test_a_repeated_tag_is_kept_once_in_its_first_spelling(self):
        assert normalize_tags(["Mali", "mali", "MALI ", "Cash"]) == ("Mali", "Cash")

    def test_order_is_kept(self):
        assert normalize_tags(["protection", "Mali"]) == ("protection", "Mali")

    def test_no_tags_is_allowed(self):
        assert normalize_tags([]) == ()

    def test_a_blank_tag_is_refused(self):
        with pytest.raises(ValueError, match="tag must not be blank"):
            normalize_tags(["Mali", "  "])

    def test_a_tag_over_the_limit_is_refused(self):
        with pytest.raises(ValueError, match="at most"):
            normalize_tag("x" * (MAX_TAG_LENGTH + 1))

    def test_more_tags_than_the_limit_are_refused(self):
        with pytest.raises(ValueError, match="at most"):
            normalize_tags([f"tag {n}" for n in range(MAX_TAGS_PER_PROJECT + 1)])

    @pytest.mark.parametrize("tag", ["Mali\tcash", "Mali⁦"])
    def test_a_tag_that_hides_its_own_text_is_refused(self, tag):
        with pytest.raises(ValueError, match="control characters"):
            normalize_tag(tag)


def _facts(**changes: object) -> ProjectFacts:
    facts = ProjectFacts(
        id=uuid.uuid4(),
        name="Cash vs vouchers, Gao",
        status=ProjectStatus.PLANNING,
        created_at=NOW - timedelta(days=10),
        last_activity_at=NOW - timedelta(days=1),
        collection_id=None,
        tags=("Mali", "cash"),
        owner_ids=frozenset({ME}),
        member_ids=frozenset({ME, OTHER}),
        relevant_papers=None,
    )
    return replace(facts, **changes)  # type: ignore[arg-type]


class TestFilters:
    def test_no_filter_keeps_every_live_project(self):
        assert ProjectListQuery().matches(_facts())

    def test_archived_projects_are_left_out_unless_asked_for(self):
        archived = _facts(status=ProjectStatus.ARCHIVED)

        assert not ProjectListQuery().matches(archived)
        assert ProjectListQuery(include_archived=True).matches(archived)

    def test_asking_for_the_archived_status_includes_archived_projects(self):
        archived = _facts(status=ProjectStatus.ARCHIVED)

        assert ProjectListQuery(statuses=frozenset({ProjectStatus.ARCHIVED})).matches(archived)

    def test_a_status_filter_keeps_only_those_statuses(self):
        query = ProjectListQuery(statuses=frozenset({ProjectStatus.FAILED}))

        assert query.matches(_facts(status=ProjectStatus.FAILED))
        assert not query.matches(_facts(status=ProjectStatus.RUNNING))

    def test_a_tag_filter_ignores_case(self):
        assert ProjectListQuery(tags=("mali",)).matches(_facts())

    def test_several_tags_must_all_be_present(self):
        assert ProjectListQuery(tags=("Mali", "cash")).matches(_facts())
        assert not ProjectListQuery(tags=("Mali", "health")).matches(_facts())

    def test_an_owner_filter_keeps_projects_that_person_owns(self):
        assert ProjectListQuery(owner_id=ME).matches(_facts())
        assert not ProjectListQuery(owner_id=OTHER).matches(_facts())

    def test_a_member_filter_keeps_projects_that_person_belongs_to(self):
        assert ProjectListQuery(member_id=OTHER).matches(_facts())
        assert not ProjectListQuery(member_id=uuid.uuid4()).matches(_facts())

    def test_a_collection_filter_keeps_that_collections_projects(self):
        collection_id = uuid.uuid4()

        assert ProjectListQuery(collection_id=collection_id).matches(
            _facts(collection_id=collection_id)
        )
        assert not ProjectListQuery(collection_id=collection_id).matches(_facts())

    def test_the_unfiled_filter_keeps_projects_in_no_collection(self):
        assert ProjectListQuery(is_unfiled=True).matches(_facts())
        assert not ProjectListQuery(is_unfiled=True).matches(_facts(collection_id=uuid.uuid4()))

    def test_one_collection_and_unfiled_together_are_refused(self):
        with pytest.raises(ValueError, match="collection"):
            ProjectListQuery(collection_id=uuid.uuid4(), is_unfiled=True)

    def test_created_dates_are_inclusive_days(self):
        created = _facts(created_at=datetime(2026, 11, 9, 23, 59, tzinfo=UTC))
        day = date(2026, 11, 9)

        assert ProjectListQuery(created_from=day, created_to=day).matches(created)
        assert not ProjectListQuery(created_from=date(2026, 11, 10)).matches(created)
        assert not ProjectListQuery(created_to=date(2026, 11, 8)).matches(created)

    def test_a_date_range_that_ends_before_it_starts_is_refused(self):
        with pytest.raises(ValueError, match="created_from"):
            ProjectListQuery(created_from=date(2026, 11, 10), created_to=date(2026, 11, 9))


class TestSorting:
    def test_last_activity_is_the_default_newest_first(self):
        old = _facts(last_activity_at=NOW - timedelta(days=5))
        new = _facts(last_activity_at=NOW)

        assert ProjectListQuery().ordered([old, new]) == [new, old]

    def test_name_sorts_alphabetically_ignoring_case(self):
        b, a = _facts(name="biometric registration"), _facts(name="Cash vs vouchers")

        assert ProjectListQuery(sort=ProjectSort.NAME).ordered([a, b]) == [b, a]

    def test_created_sorts_newest_first(self):
        old = _facts(created_at=NOW - timedelta(days=30))
        new = _facts(created_at=NOW - timedelta(days=2))

        assert ProjectListQuery(sort=ProjectSort.CREATED).ordered([old, new]) == [new, old]

    def test_most_relevant_papers_first_and_projects_without_papers_last(self):
        none, few, many = (
            _facts(relevant_papers=None),
            _facts(relevant_papers=3),
            _facts(relevant_papers=52),
        )

        query = ProjectListQuery(sort=ProjectSort.RELEVANT_PAPERS)

        assert query.ordered([none, few, many]) == [many, few, none]
