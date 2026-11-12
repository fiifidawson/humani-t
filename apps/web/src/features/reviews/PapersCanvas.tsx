/**
 * The screening results (UI_UX_SPEC §6.8): a run's papers as a real table,
 * most relevant first, filtered by label, each row opening to why it got its
 * label. Then the PRISMA counts as a table (§9.7 "a table version").
 *
 * Filter and page live in the URL, so a link opens the same view. What a
 * label means and how papers are ordered are the API's: this shows them.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Table,
	Text,
} from "@chakra-ui/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { ArrowRight, ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import {
	paperQuery,
	papersQuery,
	planVersionQuery,
	prismaQuery,
} from "../../app/reviewQueries";
import type { PapersSearch } from "../../app/router";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Canvas, CanvasHeading } from "./Canvas";
import { CriteriaList, type CriterionMark } from "./CriteriaList";
import { paperDownloads } from "./exports";
import { plural, sourceName, TIER_ORDER, TIERS } from "./format";
import { MaskedText } from "./MaskedText";
import { LabelBadge } from "./RunCards";
import { useReview, useRunNumber } from "./reviewContext";

type Schemas = components["schemas"];
type Paper = Schemas["PaperOut"];

type PaperLabel = NonNullable<PapersSearch["label"]>;

const FILTERS: readonly {
	label: PaperLabel | null;
	name: string;
	counts: readonly string[];
}[] = [
	{ label: null, name: "All", counts: [] },
	{
		label: "include",
		name: "Included",
		counts: ["highly_relevant", "relevant", "possibly_relevant"],
	},
	{ label: "exclude", name: "Excluded", counts: ["excluded"] },
	{ label: "uncertain", name: "Uncertain", counts: ["uncertain"] },
	{ label: "unscreened", name: "Not screened", counts: ["unscreened"] },
];

const FROM = "/signed-in/p/$projectId/r/$reviewId/papers" as const;

function ScreenedText({ paper, runId }: { paper: Paper; runId: string }) {
	const api = useApi();
	const { review } = useReview();
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const detail = useQuery({
		...paperQuery(api, review.id, paper.paper_id, runId),
		enabled: isOpen,
	});
	return (
		<Box>
			<DisclosureButton
				isOpen={isOpen}
				controls={id}
				onToggle={() => setOpen(!isOpen)}
			>
				Show the text that was screened
			</DisclosureButton>
			{isOpen ? (
				<Box id={id} mt="2" p="3" rounded="card" bg="bg.subtle">
					{detail.isError ? (
						<ErrorNotice error={detail.error} />
					) : detail.data === undefined ? (
						<Loading what="the text" />
					) : detail.data.masked_text === null ? (
						<Text color="fg.muted">
							This paper's text hasn't been masked yet, so it isn't shown.
						</Text>
					) : (
						<MaskedText text={detail.data.masked_text} />
					)}
				</Box>
			) : null}
		</Box>
	);
}

/** Why a paper got its label: the reason, the criteria, what was read, and by which model. */
function Why({ paper, runId }: { paper: Paper; runId: string }) {
	const api = useApi();
	const { review } = useReview();
	const decision = paper.decision;
	const version = useQuery({
		...planVersionQuery(api, review.id, decision?.criteria_version ?? 1),
		enabled: decision !== null,
	});
	const criteria = version.data?.plan.criteria ?? [];
	// A reason for exclusion is either an exclusion criterion that applied or
	// an inclusion criterion the paper missed; the words must say which.
	const listed = (ids: readonly string[], isReason: boolean) =>
		ids.map((id) => {
			const criterion = criteria.find((c) => c.id === id);
			const mark: CriterionMark = !isReason
				? "meets"
				: criterion?.kind === "inclusion"
					? "misses"
					: "excludes";
			return { key: id, mark, text: criterion?.text ?? id };
		});

	return (
		<Box
			as="section"
			aria-label={`Why: ${paper.title}`}
			py="2"
			css={{ "& > * + *": { mt: "2" } }}
		>
			{decision === null ? (
				<Text>
					Not screened: there was no abstract, or it wasn't masked yet.
				</Text>
			) : (
				<>
					<Text>
						<Text as="span" fontWeight="medium">
							Reason:{" "}
						</Text>
						{decision.rationale}
					</Text>
					<CriteriaList
						title="Criteria met"
						items={listed(decision.criteria_matched, false)}
					/>
					<CriteriaList
						title="Excluded because"
						items={listed(decision.exclusion_reasons, true)}
					/>
					{decision.is_unreadable ? (
						<Text color="caveat.fg">
							The model's answer couldn't be read twice, so this paper is marked
							uncertain.
						</Text>
					) : null}
					<Text color="fg.muted">
						Read:{" "}
						{decision.evidence_kind === "abstract"
							? "the abstract"
							: "the title only"}{" "}
						· Decided by {decision.model_id} · Criteria from plan version{" "}
						{decision.criteria_version}
					</Text>
				</>
			)}
			<Text color="fg.muted">
				Source tag {paper.tag}
				{paper.doi === null ? "" : ` · DOI ${paper.doi}`}
				{paper.found_by.length > 1
					? ` · found by ${paper.found_by.map(sourceName).join(", ")}`
					: ""}
			</Text>
			{paper.url === null ? null : (
				<ChakraLink
					href={paper.url}
					target="_blank"
					rel="noopener noreferrer"
					css={linkStyle}
				>
					Open paper
				</ChakraLink>
			)}
			<ScreenedText paper={paper} runId={runId} />
		</Box>
	);
}

function PaperRow({ paper, runId }: { paper: Paper; runId: string }) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	return (
		<>
			<Table.Row>
				<Table.Cell whiteSpace="nowrap">
					<LabelBadge tier={paper.decision?.tier ?? "unscreened"} />
				</Table.Cell>
				<Table.Cell as="th" scope="row" fontWeight="normal" textAlign="start">
					<chakra.button
						type="button"
						display="inline-flex"
						alignItems="flex-start"
						gap="1.5"
						bg="transparent"
						border="0"
						p="0"
						color="fg"
						textAlign="start"
						cursor="pointer"
						aria-expanded={isOpen}
						aria-controls={id}
						onClick={() => setOpen(!isOpen)}
						css={{ "& > svg": { flex: "none", mt: "1" } }}
					>
						{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
						{paper.title}
					</chakra.button>
				</Table.Cell>
				<Table.Cell fontVariantNumeric="tabular-nums">
					{paper.year ?? "—"}
				</Table.Cell>
				<Table.Cell>{sourceName(paper.source_id)}</Table.Cell>
			</Table.Row>
			{isOpen ? (
				<Table.Row id={id}>
					<Table.Cell colSpan={4} bg="bg.subtle">
						<Why paper={paper} runId={runId} />
					</Table.Cell>
				</Table.Row>
			) : null}
		</>
	);
}

interface FlowNote {
	text: string;
	count: number;
	/** The table filter this count opens, if any. */
	label?: PaperLabel;
}

/**
 * The PRISMA 2020 flow (prisma-statement.org/prisma-2020-flow-diagram): each
 * step's count, and what left the review at that step beneath it. The steps and
 * the counts that match a filter are buttons that show those papers.
 */
function PrismaFlow({
	runId,
	onShow,
}: {
	runId: string;
	onShow: (label: PaperLabel) => void;
}) {
	const api = useApi();
	const { review } = useReview();
	const id = useId();
	const prisma = useQuery(prismaQuery(api, review.id, runId));
	if (prisma.isError) {
		return <ErrorNotice error={prisma.error} />;
	}
	if (prisma.data === undefined) {
		return <Loading what="the PRISMA counts" />;
	}
	const counts = prisma.data;
	const steps: {
		name: string;
		count: number;
		label?: PaperLabel;
		notes: FlowNote[];
	}[] = [
		{ name: "Identified", count: counts.identified, notes: [] },
		{
			name: "Unique",
			count: counts.after_deduplication,
			notes: [
				{
					text: `${plural(counts.duplicates_removed, "duplicate")} removed`,
					count: counts.duplicates_removed,
				},
			],
		},
		{
			name: "Screened",
			count: counts.screened,
			notes: [
				{
					text: `${counts.not_screened} not screened`,
					count: counts.not_screened,
					label: "unscreened",
				},
			],
		},
		{
			name: "Included",
			count: counts.included,
			label: "include",
			notes: [
				{
					text: `${counts.excluded} excluded`,
					count: counts.excluded,
					label: "exclude",
				},
				{
					text: `${counts.uncertain} uncertain`,
					count: counts.uncertain,
					label: "uncertain",
				},
			],
		},
	];
	return (
		<Box
			as="section"
			aria-labelledby={id}
			mb="5"
			p="4"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.subtle"
		>
			<Text id={id} fontSize="xs" fontWeight="semibold" color="fg.muted" mb="3">
				PRISMA flow
			</Text>
			<chakra.ol
				aria-labelledby={id}
				display="flex"
				alignItems="flex-start"
				listStyleType="none"
				p="0"
				m="0"
			>
				{steps.map((step, index) => (
					<chakra.li
						key={step.name}
						display="flex"
						alignItems="flex-start"
						flex="1"
						minW="0"
					>
						{index > 0 ? (
							<Flex
								aria-hidden="true"
								flex="none"
								h="16"
								px="1.5"
								align="center"
								color="fg.muted"
							>
								<ArrowRight size={16} />
							</Flex>
						) : null}
						<Box flex="1" minW="0">
							<FlowBox step={step} onShow={onShow} />
							<Flex direction="column" align="flex-start" gap="1" mt="2">
								{step.notes.map((note) => (
									<FlowNoteItem key={note.text} note={note} onShow={onShow} />
								))}
							</Flex>
						</Box>
					</chakra.li>
				))}
			</chakra.ol>
		</Box>
	);
}

function FlowBox({
	step,
	onShow,
}: {
	step: { name: string; count: number; label?: PaperLabel };
	onShow: (label: PaperLabel) => void;
}) {
	const isFilter = step.label !== undefined;
	const box = (
		<Flex
			data-step=""
			direction="column-reverse"
			justify="center"
			minH="16"
			px="3"
			py="2"
			rounded="card"
			borderWidth="1px"
			borderColor={isFilter ? "brand.solid" : "border"}
			bg={isFilter ? "benefit.bg" : "bg.panel"}
			textAlign="start"
			transition="background-color 0.15s ease"
			_hover={isFilter ? { bg: "bg.muted" } : {}}
		>
			<Text as="span" fontSize="xs" color="fg.muted" lineHeight="1.3">
				{step.name}
			</Text>
			<Text
				as="span"
				fontSize="xl"
				fontWeight="semibold"
				lineHeight="1.2"
				fontVariantNumeric="tabular-nums"
			>
				{step.count}
			</Text>
		</Flex>
	);
	if (step.label === undefined) {
		return box;
	}
	const label = step.label;
	return (
		<chakra.button
			type="button"
			display="block"
			w="full"
			p="0"
			bg="transparent"
			border="0"
			cursor="pointer"
			aria-label={`${step.name} ${step.count}: show these papers`}
			onClick={() => onShow(label)}
		>
			{box}
		</chakra.button>
	);
}

function FlowNoteItem({
	note,
	onShow,
}: {
	note: FlowNote;
	onShow: (label: PaperLabel) => void;
}) {
	const label = note.label;
	if (label === undefined || note.count === 0) {
		return (
			<Text fontSize="xs" color="fg.muted" ps="1">
				{note.text}
			</Text>
		);
	}
	return (
		<Button
			size="2xs"
			variant="outline"
			rounded="pill"
			fontWeight="normal"
			onClick={() => onShow(label)}
		>
			{note.text}
			<ChevronRight />
		</Button>
	);
}

export function PapersCanvas() {
	const api = useApi();
	const navigate = useNavigate({ from: "/p/$projectId/r/$reviewId/papers" });
	const { review, runs } = useReview();
	const search = useSearch({ from: FROM });
	const runId = search.run ?? runs[0]?.id;
	const number = useRunNumber(runId ?? "");
	const label = search.label ?? null;
	const page = search.page ?? 1;
	const papers = useQuery({
		...papersQuery(api, review.id, { runId: runId ?? "", label, page }),
		enabled: runId !== undefined,
		// A new filter or page keeps the current rows until its own arrive, so
		// the panel does not flash empty with every click.
		placeholderData: keepPreviousData,
	});

	if (runId === undefined) {
		return (
			<Canvas title="Papers">
				<Text>No papers yet. They appear once a run has found them.</Text>
			</Canvas>
		);
	}
	const title = `Papers · run ${number}`;
	if (papers.isError) {
		return (
			<Canvas title={title}>
				{isApiError(papers.error, "not-found") ? (
					<Text>No papers yet. They appear once this run has found them.</Text>
				) : (
					<ErrorNotice
						error={papers.error}
						onRetry={() => void papers.refetch()}
					/>
				)}
			</Canvas>
		);
	}
	if (papers.data === undefined) {
		return (
			<Canvas title={title}>
				<Loading what="the papers" />
			</Canvas>
		);
	}
	const { counts, total, page_size: size } = papers.data;
	const sum = (keys: readonly string[]) =>
		keys.length === 0
			? Object.values(counts).reduce((a, b) => a + b, 0)
			: keys.reduce((a, key) => a + (counts[key] ?? 0), 0);
	const first = (page - 1) * size + 1;
	const last = Math.min(page * size, total);
	const go = (next: {
		label?: PaperLabel | undefined;
		page?: number | undefined;
	}) => void navigate({ search: (previous) => ({ ...previous, ...next }) });

	return (
		<Canvas
			title={title}
			downloads={paperDownloads(review.id, papers.data.run_id)}
		>
			<PrismaFlow
				runId={papers.data.run_id}
				onShow={(shown) => go({ label: shown, page: undefined })}
			/>
			<Flex role="group" aria-label="Show" gap="2" wrap="wrap">
				{FILTERS.map((filter) => (
					<Button
						key={filter.name}
						size="xs"
						variant={label === filter.label ? "solid" : "outline"}
						aria-pressed={label === filter.label}
						onClick={() =>
							go({ label: filter.label ?? undefined, page: undefined })
						}
					>
						{filter.name}{" "}
						<Text as="span" fontVariantNumeric="tabular-nums">
							{sum(filter.counts)}
						</Text>
					</Button>
				))}
			</Flex>
			<Text mt="3" color="fg.muted">
				{TIER_ORDER.filter((tier) => (counts[tier] ?? 0) > 0)
					.map((tier) => `${counts[tier]} ${TIERS[tier].word}`)
					.join(" · ")}
			</Text>

			{total === 0 ? (
				<Text mt="4">No papers have this label.</Text>
			) : (
				<Table.Root size="sm" mt="4">
					<Table.Header>
						<Table.Row>
							<Table.ColumnHeader>Label</Table.ColumnHeader>
							<Table.ColumnHeader>Title</Table.ColumnHeader>
							<Table.ColumnHeader>Year</Table.ColumnHeader>
							<Table.ColumnHeader>Source</Table.ColumnHeader>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{papers.data.papers.map((paper) => (
							<PaperRow
								key={paper.paper_id}
								paper={paper}
								runId={papers.data.run_id}
							/>
						))}
					</Table.Body>
				</Table.Root>
			)}

			{total > size ? (
				<Flex mt="4" gap="3" align="center" wrap="wrap">
					<Text flex="1" color="fg.muted">
						Showing {first}–{last} of {plural(total, "paper")}
					</Text>
					<Button
						size="xs"
						variant="outline"
						disabled={page <= 1}
						onClick={() => go({ page: page - 1 })}
					>
						Previous page
					</Button>
					<Button
						size="xs"
						variant="outline"
						disabled={last >= total}
						onClick={() => go({ page: page + 1 })}
					>
						Next page
					</Button>
				</Flex>
			) : null}

			<CanvasHeading>How this list was made</CanvasHeading>
			<Text color="fg.muted">
				Each paper's abstract was screened against the plan's criteria by the
				model. Open a row to see why it got its label.
			</Text>
		</Canvas>
	);
}
