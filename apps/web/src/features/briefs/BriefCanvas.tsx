/**
 * The decision brief, read-only, in the order the API's renderer uses.
 *
 * What the person picks as mattering most only reorders and highlights: the brief
 * has no per-cell scores and this screen invents none. Options stay in the Writer's
 * order, and nothing is ranked unless the person asks. The picks are a view
 * preference, kept in the address; "Your view first" is in ViewFirst.tsx.
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
import { Reveal } from "../../ui/Reveal";
import { useMediaQuery } from "../../ui/useMediaQuery";
import { useBriefSeen } from "../history/PaperMarks";
import { Canvas } from "../reviews/Canvas";
import { briefDownloads } from "../reviews/exports";
import { clockTime, plural } from "../reviews/format";
import { MaskedText } from "../reviews/MaskedText";
import { useReview } from "../reviews/reviewContext";
import { BriefVersions } from "./BriefVersions";
import {
	type BriefSource,
	CitationChip,
	type Claim,
	Claims,
	ClaimText,
	EvidenceBadge,
	ScoreBar,
} from "./Claim";
import { SourcePanel } from "./SourcePanel";
import {
	type Leaning,
	leaningQuery,
	leaningWords,
	ViewFirstQuestion,
} from "./ViewFirst";
import {
	CONFLICTS,
	SCORE_TONES,
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
type TradeOff = Brief["trade_offs"][number];
type RowOrder = "priorities" | "original";

// Below Chakra's `md`: the matrix becomes one card per criterion (§6.9).
const NARROW_SCREEN = "(max-width: 767px)";

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

function Header({
	brief,
	leaning,
	onOpen,
}: {
	brief: Brief;
	leaning: Leaning | null;
	onOpen: OpenSource;
}) {
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
			<BriefVersions key={brief.id} brief={brief} />
			{leaning === null ? null : (
				<Text fontSize="sm">{leaningWords(leaning)}</Text>
			)}
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
				<Reveal>
					<Box id={id} mt="2" color="fg.muted">
						<chakra.ul pl="5">
							{STRENGTH_ORDER.map((strength) => (
								<li key={strength}>
									{`${STRENGTHS[strength].word}: ${STRENGTHS[strength].meaning}`}
								</li>
							))}
						</chakra.ul>
						<Text mt="1">
							The scale follows the spirit of GRADE's certainty levels. It is
							not a GRADE rating.
						</Text>
						<Text mt="1">
							+ benefit · − risk · a chip is a cited source · reasoning is
							marked as not a source
						</Text>
					</Box>
				</Reveal>
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

/** Humanitarian criteria first, then the rest, each in the Writer's order (§6.9). */
const humanitarianFirst = (rows: readonly TradeOff[]) => [
	...rows.filter((row) => row.is_humanitarian),
	...rows.filter((row) => !row.is_humanitarian),
];

/** The rows to show; with "priorities", the picked ones move to the top. */
function orderedRows(
	brief: Brief,
	picked: readonly string[],
	isHumanitarianOnly: boolean,
	order: RowOrder,
) {
	const rows = humanitarianFirst(brief.trade_offs).filter(
		(row) => !isHumanitarianOnly || row.is_humanitarian,
	);
	return order === "original"
		? rows
		: [
				...rows.filter((row) => picked.includes(row.criterion)),
				...rows.filter((row) => !picked.includes(row.criterion)),
			];
}

function HumanitarianMark({ id }: { id?: string }) {
	return (
		<Text
			as="span"
			id={id}
			display="inline-flex"
			gap="1"
			alignItems="center"
			fontSize="xs"
			fontWeight="normal"
			color="fg.muted"
		>
			<HeartHandshake size={12} aria-hidden="true" />
			humanitarian
		</Text>
	);
}

function PriorityMark() {
	return (
		<Text as="span" fontSize="xs" color="link">
			priority
		</Text>
	);
}

/** Where a trade-off row can be linked to, as the table row or the card. */
const rowAnchor = (criterion: string) =>
	`trade-off-${criterion
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")}`;

/**
 * Which humanitarian principles the trade-offs weigh, each linked to its row, and
 * which they leave out. Coverage, never a score: the API says which is which.
 */
function Principles({ principles }: { principles: Brief["principles"] }) {
	const id = useId();
	return (
		<Box as="section" aria-labelledby={id} mb="4">
			<Heading as="h4" id={id} fontSize="sm" fontWeight="medium" mb="2">
				Humanitarian principles in this brief
			</Heading>
			<chakra.ul
				listStyleType="none"
				p="0"
				display="flex"
				flexWrap="wrap"
				gap="2"
			>
				{principles.map((principle) => (
					<Flex
						as="li"
						key={principle.name}
						align="center"
						gap="2"
						px="2.5"
						py="1"
						rounded="pill"
						borderWidth="1px"
						borderColor={
							principle.trade_off === null ? "border" : "brand.solid"
						}
						bg={principle.trade_off === null ? "transparent" : "benefit.bg"}
						fontSize="sm"
					>
						<Text as="span" fontWeight="medium">
							{principle.name}
						</Text>
						{principle.trade_off === null ? (
							<Text as="span" color="fg.muted">
								Not discussed
							</Text>
						) : (
							<ChakraLink
								href={`#${rowAnchor(principle.trade_off)}`}
								css={linkStyle}
								aria-label={`${principle.name}: weighed in the trade-offs`}
							>
								Weighed
							</ChakraLink>
						)}
					</Flex>
				))}
			</chakra.ul>
		</Box>
	);
}

/** A criterion's name with its marks, as a row header or a card heading. */
function CriterionLabel({
	row,
	isPicked,
}: {
	row: TradeOff;
	isPicked: boolean;
}) {
	return (
		<Flex direction="column" gap="0.5">
			<span>{row.criterion}</span>
			{row.is_humanitarian ? <HumanitarianMark /> : null}
			{isPicked ? <PriorityMark /> : null}
		</Flex>
	);
}

function Picker({
	brief,
	picked,
	onToggle,
}: {
	brief: Brief;
	picked: readonly string[];
	onToggle: (criterion: string) => void;
}) {
	const prefix = useId();
	return (
		<chakra.fieldset border="0" p="0" m="0">
			<chakra.legend fontWeight="medium" fontSize="sm" mb="1">
				What matters most to you?
			</chakra.legend>
			<Flex gap="3" wrap="wrap">
				{humanitarianFirst(brief.trade_offs).map((row, index) => {
					const markId = `${prefix}-${index}`;
					return (
						<Flex key={row.criterion} gap="1.5" align="center" fontSize="sm">
							<chakra.label display="inline-flex" gap="1.5" alignItems="center">
								<chakra.input
									type="checkbox"
									checked={picked.includes(row.criterion)}
									onChange={() => onToggle(row.criterion)}
									aria-describedby={row.is_humanitarian ? markId : undefined}
									accentColor="brand.solid"
									boxSize="4"
								/>
								{row.criterion}
							</chakra.label>
							{row.is_humanitarian ? <HumanitarianMark id={markId} /> : null}
						</Flex>
					);
				})}
			</Flex>
		</chakra.fieldset>
	);
}

function OrderChoice({
	order,
	onChange,
}: {
	order: RowOrder;
	onChange: (order: RowOrder) => void;
}) {
	const name = useId();
	const choices: [RowOrder, string][] = [
		["priorities", "Your priorities first"],
		["original", "Original order"],
	];
	return (
		<chakra.fieldset border="0" p="0" m="0">
			<chakra.legend fontWeight="medium" fontSize="sm" mb="1">
				Sort
			</chakra.legend>
			<Flex gap="3" wrap="wrap">
				{choices.map(([value, label]) => (
					<Flex key={value} as="label" gap="1.5" align="center" fontSize="sm">
						<chakra.input
							type="radio"
							name={name}
							checked={order === value}
							onChange={() => onChange(value)}
							accentColor="brand.solid"
							boxSize="4"
						/>
						{label}
					</Flex>
				))}
			</Flex>
		</chakra.fieldset>
	);
}

/** Narrow screens: one card per criterion, with the options as rows inside. */
function CriterionCards({
	brief,
	rows,
	picked,
	onOpen,
}: {
	brief: Brief;
	rows: readonly TradeOff[];
	picked: readonly string[];
	onOpen: OpenSource;
}) {
	const prefix = useId();
	return (
		<Stack gap="3">
			{rows.map((row, index) => {
				const isPicked = picked.includes(row.criterion);
				const headingId = `${prefix}-${index}`;
				return (
					<Box
						as="article"
						key={row.criterion}
						id={rowAnchor(row.criterion)}
						aria-labelledby={headingId}
						p="3"
						rounded="card"
						borderWidth="1px"
						borderColor="border"
						borderLeftWidth="3px"
						borderLeftColor={isPicked ? "focusRing" : "border"}
						bg="bg.panel"
					>
						<Heading as="h4" id={headingId} fontSize="sm" fontWeight="medium">
							{row.criterion}
						</Heading>
						<Flex gap="2">
							{row.is_humanitarian ? <HumanitarianMark /> : null}
							{isPicked ? <PriorityMark /> : null}
						</Flex>
						<chakra.dl mt="2">
							{brief.options.map((option) => {
								const cell = row.assessments.find(
									(a) => a.option === option.name,
								);
								return (
									<Box key={option.name} py="1">
										<chakra.dt fontWeight="medium" fontSize="sm">
											{option.name}
										</chakra.dt>
										<chakra.dd ms="0">
											{cell === undefined ? (
												"—"
											) : (
												<ClaimText
													claim={cell.claim}
													sources={brief.sources}
													onOpen={onOpen}
												/>
											)}
										</chakra.dd>
									</Box>
								);
							})}
						</chakra.dl>
					</Box>
				);
			})}
		</Stack>
	);
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
	const [order, setOrder] = useState<RowOrder>("priorities");
	const isNarrow = useMediaQuery(NARROW_SCREEN);
	const id = useId();
	const rows = orderedRows(brief, picked, isHumanitarianOnly, order);
	const considered = brief.considerations.filter((c) =>
		picked.includes(c.criterion),
	);

	return (
		<>
			<Flex gap="6" wrap="wrap" mb="3" align="flex-start">
				<Picker brief={brief} picked={picked} onToggle={onToggle} />
				<OrderChoice order={order} onChange={setOrder} />
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
			{isNarrow ? (
				<CriterionCards
					brief={brief}
					rows={rows}
					picked={picked}
					onOpen={onOpen}
				/>
			) : (
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
									<Table.Row
										key={row.criterion}
										id={rowAnchor(row.criterion)}
										verticalAlign="top"
									>
										<Table.Cell
											as="th"
											scope="row"
											textAlign="start"
											fontWeight="medium"
											borderLeftWidth="3px"
											borderLeftColor={isPicked ? "focusRing" : "transparent"}
										>
											<CriterionLabel row={row} isPicked={isPicked} />
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
			)}
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
				<Reveal>
					<Box id={id} mt="2">
						<Text color="fg.muted" mb="2">
							The model's own review of this brief.
						</Text>
						<chakra.ul
							aria-label="Quality checks"
							listStyleType="none"
							p="0"
							display="grid"
							gridTemplateColumns="repeat(auto-fill, minmax(min(100%, 15rem), 1fr))"
							columnGap="6"
							rowGap="2"
						>
							{SCORES.map(([key, name]) => (
								<Flex as="li" key={key} align="center" gap="3" fontSize="sm">
									<Text as="span" flex="1" minW="0">
										{name}
										<VisuallyHidden>: </VisuallyHidden>
									</Text>
									<ScoreBar
										value={quality[key]}
										max={2}
										tone={SCORE_TONES[quality[key]] ?? "fg.muted"}
										width="16"
									/>
									<Text as="span" w="12" color="fg.muted">
										{scoreWord(quality[key])}
									</Text>
								</Flex>
							))}
						</chakra.ul>
						<Text mt="2" fontSize="xs" color="fg.muted">
							Scored by the model that wrote the brief: a check for gaps, not a
							verification.
						</Text>
					</Box>
				</Reveal>
			) : null}
		</Box>
	);
}

function BriefView({
	brief,
	leaning,
}: {
	brief: Brief;
	leaning: Leaning | null;
}) {
	const { project, review } = useReview();
	const navigate = useNavigate();
	const search = useSearch({ from: FROM });
	const prefix = useId();
	const criteria = brief.trade_offs.map((row) => row.criterion);
	const positions = search.priorities ?? [];
	const picked = positions.flatMap((n) => criteria[n] ?? []);
	const params = { projectId: project.id, reviewId: review.id };
	const toggle = (criterion: string) => {
		const position = criteria.indexOf(criterion);
		if (position === -1) {
			return;
		}
		const next = positions.includes(position)
			? positions.filter((n) => n !== position)
			: [...positions, position];
		void navigate({
			to: "/p/$projectId/r/$reviewId/brief",
			params,
			search: (previous) => ({
				...previous,
				priorities: next.length === 0 ? undefined : next,
			}),
			replace: true,
		});
	};
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
				<Header brief={brief} leaning={leaning} onOpen={openSource} />
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
				<Principles principles={brief.principles} />
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
	const { project, review } = useReview();
	const search = useSearch({ from: FROM });
	const brief = useQuery(briefQuery(api, review.id, search.run));
	const runId = brief.data?.run_id;
	const leaning = useQuery({
		...leaningQuery(api, runId ?? ""),
		enabled: runId !== undefined,
	});
	const [skippedRun, setSkippedRun] = useState<string | null>(null);
	useBriefSeen(brief.data?.id);

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
	const isAsking = project.is_view_first_on && skippedRun !== brief.data.run_id;
	if (isAsking && leaning.isPending) {
		return (
			<Canvas title="Decision brief">
				<Loading what="the brief" />
			</Canvas>
		);
	}
	if (isAsking && leaning.data === null) {
		const { run_id: briefRun } = brief.data;
		return (
			<Canvas title="Decision brief">
				<ViewFirstQuestion
					runId={briefRun}
					options={brief.data.options.map((option) => option.name)}
					onSkip={() => setSkippedRun(briefRun)}
				/>
			</Canvas>
		);
	}
	return <BriefView brief={brief.data} leaning={leaning.data ?? null} />;
}
