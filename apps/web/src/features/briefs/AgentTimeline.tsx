/**
 * The brief's agents as a timeline (UI_UX_SPEC §6.11): every round in order,
 * each step a sentence saying what the agent did, what it found and how long it
 * took, its details opening in place. The run canvas shows it live, the trace
 * afterwards. Step outputs are the API's JSON: every field is checked before use.
 */

import { Box, chakra, Flex, Heading, Stack, Text } from "@chakra-ui/react";
import {
	ChevronDown,
	ListTree,
	LoaderCircle,
	type LucideIcon,
	PenLine,
	Scale,
	Search,
	ShieldCheck,
	X,
} from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Reveal } from "../../ui/Reveal";
import { duration, plural } from "../reviews/format";
import type { AgentStep } from "../runs/runEvents";
import { ROLES, type Role, SCORES, scoreWord } from "./words";

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
	parts.filter((part) => part !== null && part !== "").join(" · ");
const listed = (items: readonly string[], one: string, many?: string) =>
	items.length === 0 ? null : plural(items.length, one, many);

const isAccepted = (output: Output) => text(output, "verdict") === "accept";

/** What the step did or decided, as a sentence (§6.11). */
function sentenceOf(step: AgentStep): string {
	const role = ROLES[step.role];
	switch (step.status) {
		case "error":
			return `${role} failed`;
		case "refused":
			return `${role}'s model refused`;
		case "invalid":
			return `${role}'s answer could not be used`;
		case "ok":
			break;
	}
	const output = step.output ?? {};
	switch (step.role) {
		case "planner":
			return "Planner set the plan";
		case "retriever":
			return step.round === 0
				? "Retriever searched the papers"
				: "Retriever ran the Critic's searches";
		case "writer":
			return step.round <= 1
				? "Writer drafted the brief"
				: "Writer revised the brief";
		case "checks": {
			const problems = step.problems?.length ?? 0;
			return problems === 0
				? "Checks found no problems"
				: `Checks found ${plural(problems, "problem")}`;
		}
		case "critic": {
			if (isAccepted(output)) {
				return output["overruled_by_checks"] === true
					? "Critic accepted the draft, but the checks overruled it"
					: "Critic accepted the draft";
			}
			const asked = texts(output, "queries").length;
			return asked === 0
				? "Critic asked for a revision"
				: `Critic asked for a revision and ${plural(asked, "more search", "more searches")}`;
		}
	}
}

/** What the step produced or found, in one line; empty when there is nothing to add. */
function findingOf(step: AgentStep): string {
	if (step.status !== "ok") {
		return step.error ?? "";
	}
	const output = step.output ?? {};
	switch (step.role) {
		case "planner":
			return joined([
				listed(texts(output, "options"), "option"),
				listed(texts(output, "criteria"), "criterion", "criteria"),
				listed(texts(output, "sub_questions"), "sub-question"),
			]);
		case "retriever": {
			if (step.queries == null) {
				return "";
			}
			const found = step.passages_new ?? step.passages_found ?? 0;
			const all = step.passages_total;
			return joined([
				plural(step.queries.length, "search", "searches"),
				`${plural(found, "new passage")}${all == null ? "" : ` (${all} in all)`}`,
			]);
		}
		case "writer": {
			const claims = count(output, "claims");
			const sourced = count(output, "evidence_claims");
			return joined([
				listed(texts(output, "options"), "option"),
				claims === null
					? null
					: `${plural(claims, "claim")}${sourced === null ? "" : `, ${sourced} from sources`}`,
			]);
		}
		case "checks":
			return step.problems?.[0]?.message ?? "";
		case "critic":
			return texts(output, "issues")[0] ?? "";
	}
}

/** The step's details, line by line; empty when the row already says it all. */
function detailsOf(step: AgentStep): { lines: string[]; facts: string } {
	const output = step.output ?? {};
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
	const sourced = count(output, "evidence_claims");
	if (sourced !== null) {
		lines.push(
			`Claims from sources: ${sourced} of ${count(output, "claims") ?? "?"} · sources cited: ${count(output, "cited_tags") ?? 0}`,
		);
	}
	for (const problem of step.problems ?? []) {
		lines.push(`Problem: ${problem.message}`);
	}
	const scores = output["scores"];
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
	// The time is on the row already: alone, it is no reason to offer details.
	const facts = joined([
		step.model_id ?? null,
		step.prompt_version == null ? null : `prompt ${step.prompt_version}`,
		step.tokens == null
			? null
			: `${NUMBER.format(step.tokens.prompt)} tokens in, ${NUMBER.format(step.tokens.completion)} out`,
	]);
	return { lines, facts };
}

const ICONS: Readonly<Record<Role, LucideIcon>> = {
	planner: ListTree,
	retriever: Search,
	writer: PenLine,
	checks: ShieldCheck,
	critic: Scale,
};

type Look = "done" | "failed" | "running";

/** The marker on the line: the agent's icon, a cross on failure, a spinner while running. */
function Marker({ role, look }: { role: Role; look: Look }) {
	const Icon =
		look === "failed" ? X : look === "running" ? LoaderCircle : ICONS[role];
	return (
		<Flex
			aria-hidden="true"
			flex="none"
			align="center"
			justify="center"
			boxSize="7"
			rounded="full"
			borderWidth="1px"
			borderColor={look === "failed" ? "border.error" : "border.emphasized"}
			bg="bg.panel"
			color={
				look === "failed" ? "fg.error" : look === "running" ? "link" : "fg"
			}
		>
			<Icon
				size={14}
				className={look === "running" ? "timeline-spin" : undefined}
			/>
		</Flex>
	);
}

/** One entry: marker and line on the left; sentence, time, finding and details on the right. */
function Entry({
	role,
	look,
	sentence,
	seconds,
	finding,
	details,
	isLast,
}: {
	role: Role;
	look: Look;
	sentence: string;
	seconds: number | undefined;
	finding: string;
	details: ReturnType<typeof detailsOf> | null;
	isLast: boolean;
}) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const hasDetails =
		details !== null && (details.lines.length > 0 || details.facts !== "");
	return (
		<Flex as="li" gap="3" align="stretch">
			<Flex direction="column" align="center" flex="none">
				<Marker role={role} look={look} />
				{isLast ? null : (
					<Box aria-hidden="true" w="1px" flex="1" minH="3" bg="border" />
				)}
			</Flex>
			<Box flex="1" minW="0" pt="1" pb={isLast ? "0" : "4"}>
				<Flex gap="3" align="baseline" justify="space-between" wrap="wrap">
					<Text
						fontWeight="medium"
						color={look === "failed" ? "fg.error" : "fg"}
					>
						{sentence}
					</Text>
					<Flex gap="3" align="baseline" flex="none" fontSize="sm">
						{seconds === undefined || seconds < 1 ? null : (
							<Text as="span" color="fg.muted">
								{duration(seconds)}
							</Text>
						)}
						{hasDetails ? (
							<chakra.button
								type="button"
								display="inline-flex"
								alignItems="center"
								gap="1"
								p="0"
								bg="transparent"
								border="0"
								color="link"
								cursor="pointer"
								_hover={{ textDecoration: "underline" }}
								aria-expanded={isOpen}
								aria-controls={id}
								aria-label={`Details: ${sentence}`}
								onClick={() => setOpen(!isOpen)}
							>
								Details
								<ChevronDown
									size={14}
									aria-hidden="true"
									className={isOpen ? "timeline-open" : undefined}
								/>
							</chakra.button>
						) : null}
					</Flex>
				</Flex>
				{finding === "" ? null : (
					<Text fontSize="sm" color="fg.muted" overflowWrap="anywhere">
						{finding}
					</Text>
				)}
				{hasDetails && isOpen ? (
					<Reveal>
						<Stack
							id={id}
							gap="1"
							mt="2"
							pl="3"
							borderLeftWidth="2px"
							borderColor="border"
							fontSize="sm"
						>
							{details.lines.map((line) => (
								<Text key={line} overflowWrap="anywhere">
									{line}
								</Text>
							))}
							{details.facts === "" ? null : (
								<Text fontSize="xs" color="fg.muted">
									{details.facts}
								</Text>
							)}
						</Stack>
					</Reveal>
				) : null}
			</Box>
		</Flex>
	);
}

function roundName(round: number): string {
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
			if (isAccepted(output) && output["overruled_by_checks"] !== true) {
				return null;
			}
			return texts(output, "queries").length > 0
				? { round: last.round, role: "retriever" }
				: { round: last.round + 1, role: "writer" };
		}
	}
}

/** What the agent is doing now, as a sentence. */
function runningSentence({ round, role }: Place): string {
	switch (role) {
		case "planner":
			return "Planner is setting the plan";
		case "retriever":
			return round === 0
				? "Retriever is searching the papers"
				: "Retriever is running the Critic's searches";
		case "writer":
			return round <= 1
				? "Writer is drafting the brief"
				: "Writer is revising the brief";
		case "checks":
			return "Checks are running";
		case "critic":
			return "Critic is reviewing the draft";
	}
}

function Round({ round, children }: { round: number; children: ReactNode }) {
	const id = useId();
	return (
		<Box as="section" aria-labelledby={id}>
			<Heading
				as="h4"
				id={id}
				fontFamily="body"
				fontSize="sm"
				fontWeight="semibold"
				mb="3"
			>
				{roundName(round)}
			</Heading>
			<chakra.ol aria-labelledby={id} listStyleType="none" p="0" m="0">
				{children}
			</chakra.ol>
		</Box>
	);
}

/** Every round's steps in order. While `isLive`, the step being run comes last. */
export function AgentTimeline({
	steps,
	isLive,
}: {
	steps: readonly AgentStep[];
	isLive: boolean;
}) {
	const running = isLive ? runningPlace(steps) : null;
	const rounds = [
		...new Set([
			...steps.map((step) => step.round),
			...(running === null ? [] : [running.round]),
		]),
	].sort((a, b) => a - b);

	return (
		<Stack
			gap="5"
			css={{
				"& .timeline-spin": { animation: "spin 1s linear infinite" },
				"& .timeline-open": { transform: "rotate(180deg)" },
			}}
		>
			{rounds.map((round) => {
				const inRound = steps.filter((step) => step.round === round);
				const isRunningHere = running?.round === round;
				return (
					<Round key={round} round={round}>
						{inRound.map((step, index) => (
							<Entry
								// biome-ignore lint/suspicious/noArrayIndexKey: steps keep the order they ran in
								key={index}
								role={step.role}
								look={step.status === "ok" ? "done" : "failed"}
								sentence={sentenceOf(step)}
								seconds={step.seconds}
								finding={findingOf(step)}
								details={detailsOf(step)}
								isLast={index === inRound.length - 1 && !isRunningHere}
							/>
						))}
						{running !== null && isRunningHere ? (
							<Entry
								role={running.role}
								look="running"
								sentence={runningSentence(running)}
								seconds={undefined}
								finding=""
								details={null}
								isLast
							/>
						) : null}
					</Round>
				);
			})}
		</Stack>
	);
}
