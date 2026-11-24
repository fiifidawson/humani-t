/**
 * The brief's agent loop (UI_UX_SPEC §6.11), one round at a time: the Planner
 * into Retriever → Writer → Critic and back, then that round's steps, each
 * opening to its detail. The run canvas shows it live, the trace afterwards.
 * Step outputs are the API's JSON: every field is checked before it is shown.
 */

import { Box, Button, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import {
	ArrowRight,
	Check,
	ChevronUp,
	CircleDashed,
	ListTree,
	LoaderCircle,
	type LucideIcon,
	PenLine,
	Scale,
	Search,
	ShieldAlert,
	ShieldCheck,
	Undo2,
	X,
} from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Reveal } from "../../ui/Reveal";
import { duration, plural } from "../reviews/format";
import type { AgentStep } from "../runs/runEvents";
import { ROLES, type Role, SCORES, STEP_STATUSES, scoreWord } from "./words";

const NUMBER = new Intl.NumberFormat("en-GB");

type Output = Record<string, unknown>;
const text = (output: Output, key: string) =>
	typeof output[key] === "string" ? (output[key] as string) : null;
const texts = (output: Output, key: string) =>
	Array.isArray(output[key])
		? (output[key] as unknown[]).filter(
				(v): v is string => typeof v === "string",
			)
		: [];
const count = (output: Output, key: string) =>
	typeof output[key] === "number" ? (output[key] as number) : null;
const joined = (parts: readonly (string | null)[]) =>
	parts.filter((part) => part !== null).join(" · ");

/** What a finished step found, without its role; `null` when it was streamed without detail. */
function resultOf(step: AgentStep): string | null {
	if (step.seconds === undefined) {
		return null;
	}
	const output = step.output ?? {};
	switch (step.role) {
		case "planner":
			return `1 call · ${duration(step.seconds)}`;
		case "retriever": {
			const searches = plural(step.queries?.length ?? 0, "search", "searches");
			const found = step.passages_new ?? step.passages_found ?? 0;
			const all = step.passages_total;
			return `${searches} · ${plural(found, "new passage")}${all == null ? "" : ` (${all} in all)`}`;
		}
		case "writer": {
			const claims = count(output, "claims");
			return joined([
				plural(texts(output, "options").length, "option"),
				claims === null ? null : plural(claims, "claim"),
				duration(step.seconds),
			]);
		}
		case "checks": {
			const problems = step.problems?.length ?? 0;
			return problems === 0 ? "no problems" : plural(problems, "problem");
		}
		case "critic": {
			const verdict = text(output, "verdict") ?? "reviewed";
			return output["overruled_by_checks"] === true
				? `${verdict} · overruled by the checks`
				: verdict;
		}
	}
}

/** One line per step, as in the spec's timeline ("Retriever · 7 searches · 31 passages"). */
export function summaryOf(step: AgentStep): string {
	const role = ROLES[step.role];
	if (step.status !== "ok") {
		return `${role} · ${STEP_STATUSES[step.status]}${step.error == null ? "" : ` · ${step.error}`}`;
	}
	return `${role} · ${resultOf(step) ?? STEP_STATUSES.ok}`;
}

function Detail({ step }: { step: AgentStep }) {
	const output = step.output ?? {};
	const scores = output["scores"];
	const lines: string[] = [];
	const decision = text(output, "decision");
	if (decision !== null) {
		lines.push(`Decision: ${decision}`);
	}
	if (step.role === "planner" && texts(output, "options").length > 0) {
		lines.push(`Options: ${texts(output, "options").join(" · ")}`);
	}
	if (texts(output, "criteria").length > 0) {
		lines.push(`Criteria: ${texts(output, "criteria").join(", ")}`);
	}
	if (texts(output, "sub_questions").length > 0) {
		lines.push(`Sub-questions: ${texts(output, "sub_questions").join(" · ")}`);
	}
	if (step.role === "retriever" && (step.queries?.length ?? 0) > 0) {
		lines.push(`Searches: ${step.queries?.join(" · ")}`);
	}
	const evidenceClaims = count(output, "evidence_claims");
	if (evidenceClaims !== null) {
		lines.push(
			`Claims from sources: ${evidenceClaims} of ${count(output, "claims") ?? "?"} · sources cited: ${count(output, "cited_tags") ?? 0}`,
		);
	}
	for (const problem of step.problems ?? []) {
		lines.push(`Problem: ${problem.message}`);
	}
	if (typeof scores === "object" && scores !== null && !Array.isArray(scores)) {
		const known = SCORES.flatMap(([key, name]) => {
			const value = (scores as Output)[key];
			return typeof value === "number"
				? [`${name}: ${scoreWord(value)} (${value} of 2)`]
				: [];
		});
		if (known.length > 0) {
			lines.push(`Scores: ${known.join(" · ")}`);
		}
	}
	for (const issue of texts(output, "issues")) {
		lines.push(`Issue: ${issue}`);
	}
	if (step.role === "critic" && texts(output, "queries").length > 0) {
		lines.push(`Asked for: ${texts(output, "queries").join(" · ")}`);
	}
	const facts = joined([
		step.model_id ?? null,
		step.prompt_version == null ? null : `prompt ${step.prompt_version}`,
		step.tokens == null
			? null
			: `${NUMBER.format(step.tokens.prompt)} tokens in, ${NUMBER.format(step.tokens.completion)} out`,
		step.seconds === undefined ? null : duration(step.seconds),
	]);

	return (
		<Stack gap="1" mt="1.5" pl="7" color="fg.muted">
			{lines.map((line) => (
				<Text key={line} color="fg">
					{line}
				</Text>
			))}
			{facts === "" ? null : <Text fontSize="xs">{facts}</Text>}
		</Stack>
	);
}

function StepRow({ step }: { step: AgentStep }) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const isOk = step.status === "ok";
	return (
		<Box as="li" py="1.5" borderBottomWidth="1px" borderColor="border">
			<chakra.button
				type="button"
				display="flex"
				alignItems="center"
				gap="2"
				w="full"
				p="0"
				bg="transparent"
				border="0"
				color="fg"
				textAlign="start"
				cursor="pointer"
				aria-expanded={isOpen}
				aria-controls={id}
				onClick={() => setOpen(!isOpen)}
			>
				<Box
					as="span"
					color={isOk ? "brand.fg" : "fg.error"}
					display="inline-flex"
					flex="none"
				>
					{isOk ? <Check size={16} /> : <X size={16} />}
				</Box>
				{summaryOf(step)}
			</chakra.button>
			{isOpen ? (
				<Reveal>
					<Box id={id}>
						<Detail step={step} />
					</Box>
				</Reveal>
			) : null}
		</Box>
	);
}

export function roundName(round: number): string {
	return round === 0 ? "Before the first draft" : `Round ${round}`;
}

type Place = Pick<AgentStep, "round" | "role">;

/**
 * The step a live loop is on: the one after the last to finish, or `null` once
 * it has stopped. The order is the API's loop (`brief_loop.py`): the Retriever
 * runs at the end of a round, for the searches the Critic asked for.
 */
function runningPlace(steps: readonly AgentStep[]): Place | null {
	const last = steps.at(-1);
	if (last === undefined) {
		return { round: 0, role: "planner" };
	}
	// An unusable answer is asked for once more; any other failure ends the loop.
	if (last.status === "invalid") {
		return { round: last.round, role: last.role };
	}
	if (last.status !== "ok") {
		return null;
	}
	switch (last.role) {
		case "planner":
			return { round: 0, role: "retriever" };
		case "retriever":
			return { round: last.round + 1, role: "writer" };
		case "writer":
			return { round: last.round, role: "checks" };
		case "checks":
			return { round: last.round, role: "critic" };
		case "critic": {
			const output = last.output ?? {};
			if (isAccepted(output)) {
				return null;
			}
			return texts(output, "queries").length > 0
				? { round: last.round, role: "retriever" }
				: { round: last.round + 1, role: "writer" };
		}
	}
}

const isAccepted = (output: Output) =>
	text(output, "verdict") === "accept" &&
	output["overruled_by_checks"] !== true;

/** What the Critic decided, for the way back to the Retriever. */
function outcomeOf(critic: AgentStep | undefined): string | null {
	if (critic?.status !== "ok") {
		return null;
	}
	const output = critic.output ?? {};
	if (isAccepted(output)) {
		return "accepted ✓";
	}
	const asked = texts(output, "queries").length;
	const verdict = text(output, "verdict");
	const decided =
		verdict === "accept"
			? "accepted"
			: asked > 0
				? `asked for ${plural(asked, "search", "searches")}`
				: (verdict ?? "reviewed");
	return output["overruled_by_checks"] === true
		? `${decided} · overruled by the checks`
		: decided;
}

type Look = "done" | "failed" | "running" | "waiting";

const ICONS: Readonly<Record<Role, LucideIcon>> = {
	planner: ListTree,
	retriever: Search,
	writer: PenLine,
	checks: ShieldCheck,
	critic: Scale,
};

const TONES: Readonly<Record<Look, string>> = {
	done: "brand.fg",
	failed: "fg.error",
	running: "brand.fg",
	waiting: "fg.muted",
};

function StatusIcon({ look }: { look: Look }) {
	switch (look) {
		case "done":
			return <Check size={14} strokeWidth={3} />;
		case "failed":
			return <X size={14} strokeWidth={3} />;
		case "running":
			return <LoaderCircle size={14} className="loop-spin" />;
		case "waiting":
			return <CircleDashed size={14} />;
	}
}

interface Shown {
	step: AgentStep | undefined;
	look: Look;
	word: string;
}

function AgentNode({ agent, shown }: { agent: Role; shown: Shown }) {
	const Icon = ICONS[agent];
	const { step, look, word } = shown;
	const result =
		step === undefined || look === "running"
			? null
			: step.status === "ok"
				? resultOf(step)
				: (step.error ?? null);
	const isWaiting = look === "waiting";
	return (
		<Box
			data-node=""
			gridArea={agent}
			minW="0"
			px="3"
			py="2.5"
			rounded="card"
			borderWidth={look === "running" ? "2px" : "1px"}
			borderStyle={isWaiting ? "dashed" : "solid"}
			borderColor={
				look === "running"
					? "brand.solid"
					: look === "failed"
						? "border.error"
						: "border"
			}
			bg={isWaiting ? "transparent" : "bg.panel"}
			color={isWaiting ? "fg.muted" : "fg"}
			fontSize="sm"
		>
			<Flex align="center" gap="2" fontWeight="semibold">
				<Flex
					flex="none"
					boxSize="7"
					align="center"
					justify="center"
					rounded="full"
					bg={isWaiting ? "bg.muted" : "brand.subtle"}
					color={isWaiting ? "fg.muted" : "brand.fg"}
				>
					<Icon size={16} />
				</Flex>
				<span>{ROLES[agent]}</span>
			</Flex>
			<Flex align="center" gap="1.5" mt="1.5" color={TONES[look]}>
				<Flex flex="none">
					<StatusIcon look={look} />
				</Flex>
				{word}
			</Flex>
			{result === null ? null : (
				// No lone last word: "0 s" would otherwise end on a line of its own.
				<Text mt="1" textWrap="pretty">
					{result}
				</Text>
			)}
		</Box>
	);
}

/** The checks are code, not an agent: a badge on the way from the Writer to the Critic. */
function ChecksBadge({ shown }: { shown: Shown }) {
	const problems = shown.step?.problems?.length ?? 0;
	const Icon = problems > 0 ? ShieldAlert : ShieldCheck;
	const word =
		shown.look === "done"
			? problems === 0
				? "no problems"
				: plural(problems, "problem")
			: shown.word;
	return (
		<Box
			data-node=""
			maxW="8rem"
			px="2"
			py="1"
			rounded="card"
			borderWidth="1px"
			borderStyle={shown.look === "waiting" ? "dashed" : "solid"}
			borderColor={shown.look === "failed" ? "border.error" : "border"}
			bg={shown.look === "waiting" ? "transparent" : "bg.panel"}
			fontSize="xs"
			color={shown.look === "waiting" ? "fg.muted" : "fg"}
		>
			<Flex align="center" gap="1" fontWeight="semibold">
				<Flex flex="none" color={TONES[shown.look]}>
					<Icon size={14} />
				</Flex>
				<span>Checks</span>
			</Flex>
			<Flex align="center" gap="1" color={TONES[shown.look]}>
				{shown.look === "done" ? null : (
					<Flex flex="none">
						<StatusIcon look={shown.look} />
					</Flex>
				)}
				{word}
			</Flex>
		</Box>
	);
}

function Edge({ area, children }: { area: string; children?: ReactNode }) {
	return (
		<Flex
			data-loop-edge=""
			gridArea={area}
			direction="column"
			align="center"
			justify="center"
			gap="1"
			px="1.5"
			color="fg.muted"
		>
			{children}
			<Flex data-loop-arrow="">
				<ArrowRight size={16} />
			</Flex>
		</Flex>
	);
}

/**
 * The loop as a picture: four columns joined by arrows, with the way back from
 * the Critic drawn underneath. It follows its own width, as the PRISMA flow
 * does: in a narrow canvas the agents stack, the way back a line beneath them.
 */
function LoopDiagram({
	shownOf,
	outcome,
}: {
	shownOf: (role: Role) => Shown;
	outcome: string | null;
}) {
	return (
		<Box
			aria-hidden="true"
			p="3"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.subtle"
			css={{
				containerType: "inline-size",
				"& .loop-spin": { animation: "spin 1s linear infinite" },
				"@container (max-width: 44rem)": {
					"& [data-loop-grid]": {
						gridTemplateColumns: "minmax(0, 1fr)",
						gridTemplateAreas:
							'"planner" "toRetriever" "retriever" "toWriter" "writer" "toCritic" "critic" "back"',
					},
					"& [data-loop-edge]": { flexDirection: "row-reverse", py: "1" },
					"& [data-loop-arrow] svg": { transform: "rotate(90deg)" },
					"& [data-loop-back-line]": { display: "none" },
					"& [data-loop-back-label]": { justifyContent: "flex-start", mt: "2" },
				},
			}}
		>
			<Box
				data-loop-grid=""
				display="grid"
				gridTemplateColumns="minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr)"
				gridTemplateAreas='"planner toRetriever retriever toWriter writer toCritic critic" ". . back back back back back"'
			>
				<AgentNode agent="planner" shown={shownOf("planner")} />
				<Edge area="toRetriever" />
				<AgentNode agent="retriever" shown={shownOf("retriever")} />
				<Edge area="toWriter" />
				<AgentNode agent="writer" shown={shownOf("writer")} />
				<Edge area="toCritic">
					<ChecksBadge shown={shownOf("checks")} />
				</Edge>
				<AgentNode agent="critic" shown={shownOf("critic")} />
				<Box gridArea="back" minW="0" color="fg.muted">
					<Box
						data-loop-back-line=""
						position="relative"
						h="5"
						mx="10"
						borderWidth="0 2px 2px 2px"
						borderColor="border.emphasized"
						roundedBottom="md"
					>
						<Flex
							position="absolute"
							left="-9px"
							top="-10px"
							color="border.emphasized"
						>
							<ChevronUp size={16} strokeWidth={3} />
						</Flex>
					</Box>
					<Flex
						data-loop-back-label=""
						align="center"
						justify="center"
						gap="1.5"
						mt="1"
						fontSize="sm"
						textAlign="center"
					>
						<Flex flex="none">
							<Undo2 size={14} />
						</Flex>
						{outcome ?? "back if the Critic asks for more"}
					</Flex>
				</Box>
			</Box>
		</Box>
	);
}

/**
 * The loop with a round picker, then the picked round's steps. While `isLive`,
 * it follows the round being written until another is picked.
 */
export function AgentLoop({
	steps,
	isLive,
}: {
	steps: readonly AgentStep[];
	isLive: boolean;
}) {
	const id = useId();
	const [picked, setPicked] = useState<number | null>(null);
	const running = isLive ? runningPlace(steps) : null;
	const rounds = [
		...new Set([
			...steps.map((step) => step.round),
			...(running === null ? [] : [running.round]),
		]),
	].sort((a, b) => a - b);
	const shownRound = picked ?? running?.round ?? rounds.at(-1) ?? 0;
	const inRound = steps.filter((step) => step.round === shownRound);

	const shownOf = (role: Role): Shown => {
		const placed = role === "planner" ? steps : inRound;
		const step = placed.findLast((candidate) => candidate.role === role);
		const isRunning =
			running?.role === role &&
			(role === "planner" || running.round === shownRound);
		if (isRunning) {
			return { step, look: "running", word: "running" };
		}
		if (step === undefined) {
			const isAhead = running !== null && shownRound >= running.round;
			return {
				step,
				look: "waiting",
				word: isAhead ? "not reached yet" : "not run",
			};
		}
		return step.status === "ok"
			? { step, look: "done", word: STEP_STATUSES.ok }
			: { step, look: "failed", word: STEP_STATUSES[step.status] };
	};

	return (
		<Box>
			<Flex role="group" aria-label="Round" gap="2" wrap="wrap" mb="3">
				{rounds.map((round) => (
					<Button
						key={round}
						size="xs"
						variant={round === shownRound ? "solid" : "outline"}
						aria-pressed={round === shownRound}
						onClick={() => setPicked(round)}
					>
						{roundName(round)}
					</Button>
				))}
			</Flex>
			<LoopDiagram
				shownOf={shownOf}
				outcome={outcomeOf(inRound.findLast((step) => step.role === "critic"))}
			/>
			<Text
				id={`${id}-steps`}
				fontSize="xs"
				fontWeight="semibold"
				color="fg.muted"
				mt="4"
				mb="1"
			>
				{roundName(shownRound)}
			</Text>
			<chakra.ol
				aria-labelledby={`${id}-steps`}
				listStyleType="none"
				p="0"
				m="0"
				css={{ "& .loop-spin": { animation: "spin 1s linear infinite" } }}
			>
				{inRound.map((step, index) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: steps keep the order they ran in
					<StepRow key={index} step={step} />
				))}
				{running?.round === shownRound ? (
					<Flex as="li" align="center" gap="2" py="1.5" color="fg">
						<Flex flex="none" color="brand.fg">
							<LoaderCircle size={16} className="loop-spin" />
						</Flex>
						{`${ROLES[running.role]} · running`}
					</Flex>
				) : null}
			</chakra.ol>
		</Box>
	);
}
