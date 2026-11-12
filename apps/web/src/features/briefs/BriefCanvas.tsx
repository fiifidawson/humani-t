/**
 * The decision brief, read-only, in the order the API's renderer uses.
 *
 * What the person picks as mattering most only reorders and highlights: the brief
 * has no per-cell scores and this screen invents none. Options stay in the Writer's
 * order, and nothing is ranked unless the person asks.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Grid,
	Heading,
	Stack,
	Table,
	Text,
	VisuallyHidden,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { HeartHandshake, TriangleAlert } from "lucide-react";
import { type ReactNode, useCallback, useId, useState } from "react";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { briefQuery } from "../../app/briefQueries";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Canvas } from "../reviews/Canvas";
import { briefDownloads } from "../reviews/exports";
import { clockTime, plural } from "../reviews/format";
import { MaskedText } from "../reviews/MaskedText";
import { useReview } from "../reviews/reviewContext";
import {
	type BriefSource,
	CitationChip,
	type Claim,
	Claims,
	ClaimText,
	EvidenceBadge,
} from "./Claim";
import { SourcePanel } from "./SourcePanel";
import {
	CONFLICTS,
	SCORES,
	STRENGTH_ORDER,
	STRENGTHS,
	scoreWord,
	screeningWord,
	stoppedBecause,
} from "./words";

type Schemas = components["schemas"];
type Brief = Schemas["BriefOut"];
type Option = Schemas["OptionOut"];
type OpenSource = (tag: string) => void;

const FROM = "/signed-in/p/$projectId/r/$reviewId/brief" as const;

const SECTIONS = [
	["summary", "Summary"],
	["options", "Options"],
	["trade-offs", "Trade-offs"],
	["disagreements", "Disagreements"],
	["matters-most", "If this matters most"],
	["gaps", "Gaps"],
	["sources", "Sources"],
] as const;

type SectionKey = (typeof SECTIONS)[number][0];

function Section({
	prefix,
	name,
	title,
	children,
}: {
	prefix: string;
	name: SectionKey;
	title: string;
	children: ReactNode;
}) {
	return (
		<Box as="section" aria-labelledby={`${prefix}-${name}`} mt="8">
			<Heading
				as="h3"
				id={`${prefix}-${name}`}
				tabIndex={-1}
				fontFamily="body"
				fontSize="xs"
				fontWeight="normal"
				textTransform="uppercase"
				letterSpacing="0.08em"
				color="fg.muted"
				pb="2"
				mb="3"
				borderBottomWidth="1px"
				borderColor="border"
			>
				{title}
			</Heading>
			{children}
		</Box>
	);
}

/** The sticky section bar (§6.9): each entry moves focus to its section. */
function SectionBar({ prefix }: { prefix: string }) {
	const go = (name: SectionKey) => {
		const heading = document.getElementById(`${prefix}-${name}`);
		heading?.scrollIntoView?.({ block: "start" });
		heading?.focus({ preventScroll: true });
	};
	return (
		<Box
			as="nav"
			aria-label="Sections of this brief"
			position="sticky"
			top="-5"
			zIndex="1"
			bg="bg"
			py="2"
			mb="2"
			borderBottomWidth="1px"
			borderColor="border"
		>
			<Flex as="ul" listStyleType="none" p="0" m="0" gap="1" wrap="wrap">
				{SECTIONS.map(([name, label]) => (
					<li key={name}>
						<Button
							size="xs"
							variant="outline"
							rounded="pill"
							onClick={() => go(name)}
						>
							{label}
						</Button>
					</li>
				))}
			</Flex>
		</Box>
	);
}

function Header({ brief, onOpen }: { brief: Brief; onOpen: OpenSource }) {
	const { project, review } = useReview();
	return (
		<Stack gap="4">
			<Text
				fontSize="xs"
				color="fg.muted"
				textTransform="uppercase"
				letterSpacing="0.08em"
			>
				{[
					`Version ${brief.version}`,
					clockTime(brief.created_at),
					`Made by ${brief.model_id}`,
					`kept round ${brief.kept_round} of ${brief.rounds}`,
					stoppedBecause(brief.stopped_because, brief.max_rounds),
				].join(" · ")}
			</Text>
			<Box>
				<Text fontSize="xs" color="fg.muted" mb="1">
					The question as the model received it
				</Text>
				<Box fontFamily="heading" fontSize="xl" lineHeight="1.3">
					<MaskedText text={brief.question} />
				</Box>
			</Box>
			<Box>
				<Text fontWeight="semibold" mb="1">
					Bottom line
				</Text>
				<Claims
					claims={brief.bottom_line}
					sources={brief.sources}
					onOpen={onOpen}
				/>
			</Box>
			<ChakraLink asChild css={linkStyle} alignSelf="flex-start">
				<Link
					to="/p/$projectId/r/$reviewId/trace"
					params={{ projectId: project.id, reviewId: review.id }}
					search={{ run: brief.run_id }}
				>
					How this was made
				</Link>
			</ChakraLink>
			<Box
				role="note"
				p="3"
				rounded="card"
				borderWidth="1px"
				borderColor="border.warning"
				bg="caveat.subtle"
				color="fg"
			>
				<Flex gap="2" align="flex-start">
					<Box as="span" color="caveat.fg" mt="1" flex="none">
						<TriangleAlert size={16} />
					</Box>
					<Text>{brief.caveat}</Text>
				</Flex>
				{brief.warnings.length === 0 ? null : (
					<Box mt="2" pl="6">
						<Text fontWeight="medium">Checks still failing:</Text>
						<chakra.ul pl="5">
							{brief.warnings.map((warning) => (
								<li key={`${warning.code}-${warning.where ?? ""}`}>
									{warning.message}
								</li>
							))}
						</chakra.ul>
					</Box>
				)}
			</Box>
		</Stack>
	);
}

function StrengthHelp() {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	return (
		<Box mb="3">
			<DisclosureButton
				isOpen={isOpen}
				controls={id}
				onToggle={() => setOpen(!isOpen)}
			>
				What the strengths mean
			</DisclosureButton>
			{isOpen ? (
				<Box id={id} mt="2" color="fg.muted">
					<chakra.ul pl="5">
						{STRENGTH_ORDER.map((strength) => (
							<li key={strength}>
								{`${STRENGTHS[strength].word}: ${STRENGTHS[strength].meaning}`}
							</li>
						))}
					</chakra.ul>
					<Text mt="1">
						The scale follows the spirit of GRADE's certainty levels. It is not
						a GRADE rating.
					</Text>
					<Text mt="1">
						+ benefit · − risk · a chip is a cited source · reasoning is marked
						as not a source
					</Text>
				</Box>
			) : null}
		</Box>
	);
}

function ClaimList({
	label,
	sign,
	spoken,
	claims,
	tone,
	sources,
	onOpen,
}: {
	label: string;
	sign: string;
	spoken: string;
	claims: readonly Claim[];
	tone: string;
	sources: readonly BriefSource[];
	onOpen: OpenSource;
}) {
	const id = useId();
	if (claims.length === 0) {
		return null;
	}
	return (
		<Box mt="3">
			<Text id={id} fontWeight="semibold" fontSize="sm" color={tone}>
				{label}
			</Text>
			<chakra.ul aria-labelledby={id} listStyleType="none" p="0" mt="1">
				{claims.map((claim, index) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: claims keep the Writer's order
					<Flex as="li" key={index} gap="2" align="baseline" py="0.5">
						<Box
							as="span"
							aria-hidden="true"
							color={tone}
							fontWeight="bold"
							flex="none"
						>
							{sign}
						</Box>
						<Box as="span">
							<VisuallyHidden>{spoken} </VisuallyHidden>
							<ClaimText claim={claim} sources={sources} onOpen={onOpen} />
						</Box>
					</Flex>
				))}
			</chakra.ul>
		</Box>
	);
}

const citedTags = (option: Option) =>
	new Set(
		[...option.benefits, ...option.risks].flatMap((c) =>
			c.citations.map((x) => x.tag),
		),
	).size;

function OptionCard({
	option,
	priority,
	sources,
	onOpen,
}: {
	option: Option;
	priority: string | null;
	sources: readonly BriefSource[];
	onOpen: OpenSource;
}) {
	const id = useId();
	return (
		<Box
			as="article"
			aria-labelledby={id}
			p="4"
			rounded="card"
			borderWidth={priority === null ? "1px" : "2px"}
			borderColor={priority === null ? "border" : "focusRing"}
			bg="bg.panel"
		>
			<Flex justify="space-between" gap="3" wrap="wrap" align="baseline">
				<Heading as="h4" id={id} fontSize="md" fontWeight="semibold">
					{option.name}
				</Heading>
				<EvidenceBadge strength={option.strength} />
			</Flex>
			{priority === null ? null : (
				<Text fontSize="xs" color="link" mt="1">
					Fits your priority: {priority}
				</Text>
			)}
			<Text mt="2">{option.description}</Text>
			<ClaimList
				label="Benefits"
				sign="+"
				spoken="Benefit:"
				claims={option.benefits}
				tone="benefit.fg"
				sources={sources}
				onOpen={onOpen}
			/>
			<ClaimList
				label="Risks"
				sign="−"
				spoken="Risk:"
				claims={option.risks}
				tone="risk.fg"
				sources={sources}
				onOpen={onOpen}
			/>
			<Text mt="3" fontSize="xs" color="fg.muted">
				{plural(citedTags(option), "source")}
			</Text>
		</Box>
	);
}

/** Picked first, then humanitarian criteria, then the rest, each in the Writer's order (§6.9). */
function orderedRows(
	brief: Brief,
	picked: readonly string[],
	isHumanitarianOnly: boolean,
) {
	const rows = brief.trade_offs.filter(
		(row) => !isHumanitarianOnly || row.is_humanitarian,
	);
	const rank = (row: Brief["trade_offs"][number]) =>
		picked.includes(row.criterion) ? 0 : row.is_humanitarian ? 1 : 2;
	return [...rows].sort((a, b) => rank(a) - rank(b));
}

function TradeOffs({
	brief,
	picked,
	onToggle,
	onOpen,
	labelId,
}: {
	brief: Brief;
	picked: readonly string[];
	onToggle: (criterion: string) => void;
	onOpen: OpenSource;
	labelId: string;
}) {
	const [isHumanitarianOnly, setHumanitarianOnly] = useState(false);
	const id = useId();
	const rows = orderedRows(brief, picked, isHumanitarianOnly);
	const considered = brief.considerations.filter((c) =>
		picked.includes(c.criterion),
	);

	return (
		<>
			<Flex gap="6" wrap="wrap" mb="3" align="flex-start">
				<chakra.fieldset border="0" p="0" m="0">
					<chakra.legend fontWeight="medium" fontSize="sm" mb="1">
						What matters most to you?
					</chakra.legend>
					<Flex gap="3" wrap="wrap">
						{brief.trade_offs.map((row) => (
							<Flex
								key={row.criterion}
								as="label"
								gap="1.5"
								align="center"
								fontSize="sm"
							>
								<chakra.input
									type="checkbox"
									checked={picked.includes(row.criterion)}
									onChange={() => onToggle(row.criterion)}
									accentColor="brand.solid"
									boxSize="4"
								/>
								{row.criterion}
							</Flex>
						))}
					</Flex>
				</chakra.fieldset>
				<Flex as="label" gap="1.5" align="center" fontSize="sm">
					<chakra.input
						type="checkbox"
						checked={isHumanitarianOnly}
						onChange={() => setHumanitarianOnly(!isHumanitarianOnly)}
						accentColor="brand.solid"
						boxSize="4"
					/>
					Show humanitarian criteria only
				</Flex>
			</Flex>
			{considered.length === 0 ? null : (
				<Stack
					gap="1"
					mb="3"
					p="3"
					rounded="card"
					bg="bg.subtle"
					aria-live="polite"
				>
					{considered.map((c) => (
						<Text key={`${c.criterion}-${c.option}`}>
							{`If ${c.criterion} matters most: ${c.option}, because `}
							<ClaimText
								claim={c.because}
								sources={brief.sources}
								onOpen={onOpen}
							/>
						</Text>
					))}
				</Stack>
			)}
			<Box overflowX="auto">
				<Table.Root size="sm" aria-labelledby={labelId} id={id}>
					<Table.Header>
						<Table.Row>
							<Table.ColumnHeader>Criterion</Table.ColumnHeader>
							{brief.options.map((option) => (
								<Table.ColumnHeader key={option.name}>
									{option.name}
								</Table.ColumnHeader>
							))}
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{rows.map((row) => {
							const isPicked = picked.includes(row.criterion);
							return (
								<Table.Row key={row.criterion} verticalAlign="top">
									<Table.Cell
										as="th"
										scope="row"
										textAlign="start"
										fontWeight="medium"
										borderLeftWidth="3px"
										borderLeftColor={isPicked ? "focusRing" : "transparent"}
									>
										<Flex direction="column" gap="0.5">
											<span>{row.criterion}</span>
											{row.is_humanitarian ? (
												<Text
													as="span"
													display="inline-flex"
													gap="1"
													alignItems="center"
													fontSize="xs"
													fontWeight="normal"
													color="fg.muted"
												>
													<HeartHandshake size={12} />
													humanitarian
												</Text>
											) : null}
											{isPicked ? (
												<Text as="span" fontSize="xs" color="link">
													priority
												</Text>
											) : null}
										</Flex>
									</Table.Cell>
									{brief.options.map((option) => {
										const cell = row.assessments.find(
											(a) => a.option === option.name,
										);
										return (
											<Table.Cell key={option.name}>
												{cell === undefined ? (
													"—"
												) : (
													<ClaimText
														claim={cell.claim}
														sources={brief.sources}
														onOpen={onOpen}
													/>
												)}
											</Table.Cell>
										);
									})}
								</Table.Row>
							);
						})}
					</Table.Body>
				</Table.Root>
			</Box>
		</>
	);
}

function Disagreement({
	conflict,
	sources,
	onOpen,
}: {
	conflict: Brief["conflicts"][number];
	sources: readonly BriefSource[];
	onOpen: OpenSource;
}) {
	const id = useId();
	const { words, help } = CONFLICTS[conflict.conflict_type];
	return (
		<Box
			as="article"
			aria-labelledby={id}
			p="4"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
		>
			<Heading as="h4" id={id} fontSize="md" fontWeight="semibold">
				{conflict.question}
			</Heading>
			<Text mt="1" fontWeight="medium">
				{words}
			</Text>
			<Text fontSize="xs" color="fg.muted">
				{help}
			</Text>
			<VisuallyHidden id={`${id}-positions`}>Positions</VisuallyHidden>
			<Grid
				as="ul"
				aria-labelledby={`${id}-positions`}
				listStyleType="none"
				p="0"
				mt="3"
				gap="3"
				templateColumns={{
					base: "1fr",
					md: "repeat(auto-fit, minmax(200px, 1fr))",
				}}
			>
				{conflict.positions.map((position, index) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: positions keep the Writer's order
					<Box as="li" key={index} p="3" rounded="card" bg="bg.subtle">
						<Text as="span" fontSize="xs" color="fg.muted">
							Position {index + 1}:{" "}
						</Text>
						<ClaimText claim={position} sources={sources} onOpen={onOpen} />
					</Box>
				))}
			</Grid>
			{conflict.meaning === null ? null : (
				<Text mt="3">
					What this means:{" "}
					<ClaimText
						claim={conflict.meaning}
						sources={sources}
						onOpen={onOpen}
					/>
				</Text>
			)}
		</Box>
	);
}

function Sources({
	brief,
	onOpen,
	labelId,
}: {
	brief: Brief;
	onOpen: OpenSource;
	labelId: string;
}) {
	return (
		<chakra.ol aria-labelledby={labelId} listStyleType="none" p="0">
			{brief.sources.map((source) => (
				<Flex
					as="li"
					key={source.tag}
					gap="2"
					align="baseline"
					wrap="wrap"
					py="1.5"
					borderBottomWidth="1px"
					borderColor="border"
				>
					<CitationChip tag={source.tag} source={source} onOpen={onOpen} />
					<Text as="span">{source.title ?? "Untitled source"}</Text>
					<Text as="span" color="fg.muted">
						{[
							source.year,
							source.doi === null ? null : `DOI ${source.doi}`,
							screeningWord(source.screening_label),
							`cited ${plural(source.citations, "time")}`,
						]
							.filter((part) => part !== null)
							.join(" · ")}
					</Text>
				</Flex>
			))}
		</chakra.ol>
	);
}

function Quality({ quality }: { quality: NonNullable<Brief["quality"]> }) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	return (
		<Box mt="8">
			<DisclosureButton
				isOpen={isOpen}
				controls={id}
				onToggle={() => setOpen(!isOpen)}
			>
				Show quality checks
			</DisclosureButton>
			{isOpen ? (
				<Box id={id} mt="2">
					<Text color="fg.muted" mb="2">
						The model's own review of this brief.
					</Text>
					<chakra.ul
						listStyleType="none"
						p="0"
						display="flex"
						flexWrap="wrap"
						columnGap="5"
						rowGap="1"
					>
						{SCORES.map(([key, name]) => (
							<li key={key}>
								{`${name}: ${scoreWord(quality[key])} (${quality[key]} of 2)`}
							</li>
						))}
					</chakra.ul>
					<Text mt="2" fontSize="xs" color="fg.muted">
						These scores come from the same model that wrote the brief. They
						catch gaps and weak citations, not every error. They are not a
						verification.
					</Text>
				</Box>
			) : null}
		</Box>
	);
}

function BriefView({ brief }: { brief: Brief }) {
	const { project, review } = useReview();
	const navigate = useNavigate();
	const search = useSearch({ from: FROM });
	const prefix = useId();
	const [picked, setPicked] = useState<string[]>([]);
	const toggle = (criterion: string) =>
		setPicked((current) =>
			current.includes(criterion)
				? current.filter((c) => c !== criterion)
				: [...current, criterion],
		);
	const params = { projectId: project.id, reviewId: review.id };
	const openSource = (tag: string) =>
		void navigate({
			to: "/p/$projectId/r/$reviewId/brief",
			params,
			search: (previous) => ({ ...previous, source: tag }),
		});
	const closeSource = useCallback(
		() =>
			void navigate({
				to: "/p/$projectId/r/$reviewId/brief",
				params: { projectId: project.id, reviewId: review.id },
				search: (previous) => ({ ...previous, source: undefined }),
			}),
		[navigate, project.id, review.id],
	);
	const priorityFor = (option: Option) =>
		brief.considerations.find(
			(c) => c.option === option.name && picked.includes(c.criterion),
		)?.criterion ?? null;

	return (
		<Canvas
			title="Decision brief"
			downloads={briefDownloads(review.id, brief.run_id)}
			panel={
				search.source === undefined ? null : (
					<SourcePanel
						key={search.source}
						briefId={brief.id}
						tag={search.source}
						source={brief.sources.find((s) => s.tag === search.source)}
						onClose={closeSource}
					/>
				)
			}
		>
			<SectionBar prefix={prefix} />
			<Section prefix={prefix} name="summary" title="Summary">
				<Header brief={brief} onOpen={openSource} />
			</Section>

			<Section prefix={prefix} name="options" title="Options">
				<StrengthHelp />
				<Grid gap="3" templateColumns="repeat(auto-fit, minmax(240px, 1fr))">
					{brief.options.map((option) => (
						<OptionCard
							key={option.name}
							option={option}
							priority={priorityFor(option)}
							sources={brief.sources}
							onOpen={openSource}
						/>
					))}
				</Grid>
			</Section>

			<Section prefix={prefix} name="trade-offs" title="Trade-offs">
				<TradeOffs
					brief={brief}
					picked={picked}
					onToggle={toggle}
					onOpen={openSource}
					labelId={`${prefix}-trade-offs`}
				/>
			</Section>

			<Section
				prefix={prefix}
				name="disagreements"
				title={`Where the evidence disagrees (${brief.conflicts.length})`}
			>
				{brief.conflicts.length === 0 ? (
					<Text color="fg.muted">
						The sources this brief cites don't disagree.
					</Text>
				) : (
					<Stack gap="3">
						{brief.conflicts.map((conflict) => (
							<Disagreement
								key={conflict.question}
								conflict={conflict}
								sources={brief.sources}
								onOpen={openSource}
							/>
						))}
					</Stack>
				)}
			</Section>

			<Section prefix={prefix} name="matters-most" title="If this matters most">
				{brief.considerations.length === 0 ? (
					<Text color="fg.muted">
						The brief names no option for any one criterion.
					</Text>
				) : (
					<chakra.ul
						aria-labelledby={`${prefix}-matters-most`}
						listStyleType="none"
						p="0"
					>
						{brief.considerations.map((c) => (
							<Box as="li" key={`${c.criterion}-${c.option}`} py="1.5">
								<chakra.button
									type="button"
									aria-pressed={picked.includes(c.criterion)}
									onClick={() => toggle(c.criterion)}
									bg={picked.includes(c.criterion) ? "bg.muted" : "transparent"}
									border="1px solid"
									borderColor="border.emphasized"
									rounded="pill"
									px="2.5"
									minH="6"
									fontWeight="medium"
									cursor="pointer"
								>
									{c.criterion}
								</chakra.button>{" "}
								→ {c.option} because{" "}
								<ClaimText
									claim={c.because}
									sources={brief.sources}
									onOpen={openSource}
								/>
							</Box>
						))}
					</chakra.ul>
				)}
			</Section>

			<Section
				prefix={prefix}
				name="gaps"
				title={`Gaps in the evidence (${brief.gaps.length})`}
			>
				{brief.gaps.length === 0 ? (
					<Text color="fg.muted">The brief names no gaps.</Text>
				) : (
					<chakra.ul pl="5">
						{brief.gaps.map((gap) => (
							<li key={gap}>{gap}</li>
						))}
					</chakra.ul>
				)}
			</Section>

			<Section prefix={prefix} name="sources" title="Sources">
				<Sources
					brief={brief}
					onOpen={openSource}
					labelId={`${prefix}-sources`}
				/>
			</Section>

			{brief.quality === null ? null : <Quality quality={brief.quality} />}
		</Canvas>
	);
}

export function BriefCanvas() {
	const api = useApi();
	const { review } = useReview();
	const search = useSearch({ from: FROM });
	const brief = useQuery(briefQuery(api, review.id, search.run));

	if (isApiError(brief.error, "not-found")) {
		return (
			<Canvas title="Decision brief">
				<Text>
					No decision brief yet. It appears when a run's Brief stage finishes.
				</Text>
			</Canvas>
		);
	}
	if (brief.isError) {
		return (
			<Canvas title="Decision brief">
				<ErrorNotice error={brief.error} onRetry={() => void brief.refetch()} />
			</Canvas>
		);
	}
	if (brief.data === undefined) {
		return (
			<Canvas title="Decision brief">
				<Loading what="the brief" />
			</Canvas>
		);
	}
	return <BriefView brief={brief.data} />;
}
