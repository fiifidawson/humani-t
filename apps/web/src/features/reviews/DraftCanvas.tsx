/**
 * The search draft canvas (UI_UX_SPEC §6.5): every category, concept and
 * synonym, who added what, what changed from the draft before, the criteria,
 * and the exact query. It is also the vocabulary review: "Accept and search"
 * here is the same confirmation as on the draft card.
 *
 * Read-only: drafts change through the chat (§6.5 "Editing").
 */

import { Box, Button, Code, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { MessageSquare, Pencil } from "lucide-react";
import { useId } from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { planVersionQuery } from "../../app/reviewQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";

import { Loading } from "../../ui/Loading";
import { fullDate } from "../projects/format";
import { Canvas, CanvasHeading } from "./Canvas";
import { CriteriaList } from "./CriteriaList";
import { AcceptButton, AcceptProblem } from "./PlanCards";
import { useReview, type Version } from "./reviewContext";

type Schemas = components["schemas"];

function Origin({ term }: { term: Schemas["TermOut"] }) {
	const { userId } = useReview();
	if (term.added_by.kind !== "person") {
		return null;
	}
	return (
		<Text
			as="span"
			display="inline-flex"
			alignItems="center"
			gap="1"
			ml="1.5"
			fontSize="xs"
			color="fg.muted"
		>
			<Pencil size={12} />
			{term.added_by.user_id === userId ? "added by you" : "added by a member"}
		</Text>
	);
}

function Concept({ concept }: { concept: Schemas["ConceptOut"] }) {
	const [head, ...synonyms] = concept.terms;
	return (
		<li>
			<Text as="span" fontWeight="medium">
				{concept.term}
			</Text>
			{head === undefined ? null : <Origin term={head} />}
			{synonyms.length === 0 ? null : (
				<>
					{" "}
					<Text as="span" color="fg.muted">
						or:{" "}
					</Text>
					{synonyms.map((term, index) => (
						<span key={term.text}>
							{index === 0 ? "" : ", "}
							{term.text}
							<Origin term={term} />
						</span>
					))}
				</>
			)}
		</li>
	);
}

function Changes({ diff }: { diff: Schemas["DiffOut"] }) {
	const id = useId();
	const lines = [
		...diff.concepts.map(
			(c) =>
				`${c.change === "added" ? "+ added" : "− removed"} concept ${c.term} (${c.theme})`,
		),
		...diff.terms.map(
			(t) =>
				`${t.change === "added" ? "+ added" : "− removed"} ${t.term} (${t.concept})`,
		),
		...diff.criteria.map(
			(c) =>
				`${c.change === "added" ? "+ added" : "− removed"} criterion: ${c.text}`,
		),
	];
	if (diff.from_version === null) {
		return null;
	}
	return (
		<>
			<CanvasHeading id={id}>
				Changes from draft {diff.from_version}
			</CanvasHeading>
			{lines.length === 0 ? (
				<Text color="fg.muted">No terms changed.</Text>
			) : (
				<chakra.ul aria-labelledby={id} pl="5">
					{lines.map((line) => (
						<li key={line}>{line}</li>
					))}
				</chakra.ul>
			)}
		</>
	);
}

function titleOf(version: Version, latest: number): string {
	if (version.accepted_at !== null) {
		return `Search draft · version ${version.version} · accepted`;
	}
	if (version.version < latest) {
		return `Search draft · version ${version.version} · replaced by version ${latest}`;
	}
	return `Search draft · version ${version.version} (not accepted yet)`;
}

function Draft({
	version,
	diff,
}: {
	version: Version;
	diff: Schemas["DiffOut"] | undefined;
}) {
	const { review, plan, canManage, runs, focusComposer } = useReview();
	const latest = plan.current?.version ?? version.version;
	const isOpen = version.accepted_at === null && version.version === latest;
	const categories = version.themes;

	return (
		<Canvas title={titleOf(version, latest)}>
			<Stack gap="2">
				<Text>Your question: {review.question}</Text>
				<Text color="fg.muted">
					A paper must match at least one concept in every category. The words
					in a concept are alternatives (OR).
				</Text>
				{version.is_strict ? (
					<Text>Your terms, used exactly as given.</Text>
				) : null}
			</Stack>

			{categories.map((theme, index) => (
				<Box
					key={theme.name}
					as="section"
					aria-labelledby={`category-${index}`}
					mt="5"
					p="4"
					rounded="card"
					borderWidth="1px"
					borderColor="border"
					bg="bg.panel"
				>
					<Text as="h3" id={`category-${index}`} fontWeight="semibold" mb="2">
						Category {index + 1} · {theme.name}
					</Text>
					<chakra.ul pl="5" css={{ "& > li + li": { mt: "1" } }}>
						{theme.concepts.map((concept) => (
							<Concept key={concept.term} concept={concept} />
						))}
					</chakra.ul>
				</Box>
			))}

			{version.notes.length === 0 ? null : (
				<>
					<CanvasHeading>
						Changes made automatically ({version.notes.length})
					</CanvasHeading>
					<chakra.ul pl="5">
						{version.notes.map((note) => (
							<li key={note}>{note}</li>
						))}
					</chakra.ul>
				</>
			)}

			{diff === undefined ? null : <Changes diff={diff} />}

			<CanvasHeading>Screening criteria</CanvasHeading>
			<Stack gap="4">
				<CriteriaList
					title="A paper is included only if it meets all of these"
					items={version.criteria
						.filter((c) => c.kind === "inclusion")
						.map((c) => ({ key: c.id, mark: "meets", text: c.text }))}
				/>
				<CriteriaList
					title="A paper is excluded if any of these apply"
					items={version.criteria
						.filter((c) => c.kind === "exclusion")
						.map((c) => ({ key: c.id, mark: "excludes", text: c.text }))}
				/>
			</Stack>

			<CanvasHeading>The exact query</CanvasHeading>
			<Code display="block" whiteSpace="pre-wrap" overflowWrap="anywhere" p="3">
				{version.query.rendered}
			</Code>
			<Text mt="1" fontSize="xs" color="fg.muted">
				{version.query.term_count} terms. Each source receives its own form of
				this query.
			</Text>

			<Box mt="6" pt="4" borderTopWidth="1px" borderColor="border">
				{isOpen && canManage ? (
					<Flex gap="2" align="center" wrap="wrap">
						<AcceptButton version={version.version} />
						{version.is_strict ? null : (
							<Button size="sm" variant="outline" onClick={focusComposer}>
								<MessageSquare />
								Ask for changes in the chat
							</Button>
						)}
					</Flex>
				) : null}
				{version.accepted_at === null ? null : (
					<Text>
						Version {version.version} · accepted {fullDate(version.accepted_at)}
						{runs.length > 0 ? ` · used in run ${runs.length}` : ""}
					</Text>
				)}
				<Box mt="3">
					<AcceptProblem />
				</Box>
				<Text mt="3" fontSize="xs" color="fg.muted">
					{version.model_id === null
						? "Written by a person."
						: `Drafted by ${version.model_id}.`}
				</Text>
			</Box>
		</Canvas>
	);
}

export function DraftCanvas() {
	const api = useApi();
	const { review, plan } = useReview();
	const search = useSearch({
		from: "/signed-in/p/$projectId/r/$reviewId/draft",
	});
	const current = plan.current;
	const number = search.version ?? current?.version;
	const isCurrent = current !== null && number === current.version;
	const detail = useQuery({
		...planVersionQuery(api, review.id, number ?? 1),
		enabled: number !== undefined && (!isCurrent || number > 1),
	});

	if (number === undefined) {
		return (
			<Canvas title="Search draft">
				<Text>
					There is no draft yet. It appears once the search planner has made
					one.
				</Text>
			</Canvas>
		);
	}
	if (isCurrent) {
		return <Draft version={current} diff={detail.data?.diff} />;
	}
	if (detail.isError) {
		return (
			<Canvas title="Search draft">
				<ErrorNotice error={detail.error} />
			</Canvas>
		);
	}
	if (detail.data === undefined) {
		return (
			<Canvas title="Search draft">
				<Loading what="the draft" />
			</Canvas>
		);
	}
	return <Draft version={detail.data.plan} diff={detail.data.diff} />;
}
