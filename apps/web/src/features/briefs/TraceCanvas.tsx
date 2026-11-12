/**
 * How the brief was made: the agent loop as one row per step, grouped by round.
 *
 * While the Brief stage runs there is no trace yet, so steps come from the run's
 * stream. Step outputs are the API's JSON: every field is checked first, and
 * anything unexpected is simply not shown.
 */

import { Box, chakra, Flex, Heading, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { Check, X } from "lucide-react";
import { useId, useState } from "react";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { briefQuery, traceQuery } from "../../app/briefQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Canvas, CanvasHeading } from "../reviews/Canvas";
import { duration, plural } from "../reviews/format";
import { useLiveRun } from "../reviews/liveRun";
import { useReview } from "../reviews/reviewContext";
import {
	ROLES,
	SCORES,
	STEP_STATUSES,
	scoreWord,
	stoppedBecause,
} from "./words";

type Schemas = components["schemas"];
type Step = Schemas["AgentStepOut"];
type Trace = Schemas["TraceOut"];

const TITLE = "How this brief was made";
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

/** One line per step, as in the spec's timeline ("Retriever · 7 searches · 31 passages"). */
export function summaryOf(step: Step): string {
	const role = ROLES[step.role];
	if (step.status !== "ok") {
		return `${role} · ${STEP_STATUSES[step.status]}${step.error == null ? "" : ` · ${step.error}`}`;
	}
	const output = step.output ?? {};
	switch (step.role) {
		case "planner":
			return `${role} · 1 call · ${duration(step.seconds)}`;
		case "retriever": {
			const searches = plural(step.queries?.length ?? 0, "search", "searches");
			const found = step.passages_new ?? step.passages_found ?? 0;
			const all = step.passages_total;
			return `${role} · ${searches} · ${plural(found, "new passage")}${all == null ? "" : ` (${all} in all)`}`;
		}
		case "writer": {
			const claims = count(output, "claims");
			return [
				role,
				plural(texts(output, "options").length, "option"),
				claims === null ? null : plural(claims, "claim"),
				duration(step.seconds),
			]
				.filter((part) => part !== null)
				.join(" · ");
		}
		case "checks": {
			const problems = step.problems?.length ?? 0;
			return `${role} · ${problems === 0 ? "no problems" : plural(problems, "problem")}`;
		}
		case "critic": {
			const verdict = text(output, "verdict") ?? "reviewed";
			return output["overruled_by_checks"] === true
				? `${role} · ${verdict} · overruled by the checks`
				: `${role} · ${verdict}`;
		}
	}
}

function Detail({ step }: { step: Step }) {
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
	const facts = [
		step.model_id == null ? null : step.model_id,
		step.prompt_version == null ? null : `prompt ${step.prompt_version}`,
		step.tokens == null
			? null
			: `${NUMBER.format(step.tokens.prompt)} tokens in, ${NUMBER.format(step.tokens.completion)} out`,
		duration(step.seconds),
	].filter((part) => part !== null);

	return (
		<Stack gap="1" mt="1.5" pl="7" color="fg.muted">
			{lines.map((line) => (
				<Text key={line} color="fg">
					{line}
				</Text>
			))}
			<Text fontSize="xs">{facts.join(" · ")}</Text>
		</Stack>
	);
}

function StepRow({ step }: { step: Step }) {
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
				<Box id={id}>
					<Detail step={step} />
				</Box>
			) : null}
		</Box>
	);
}

function roundName(round: number): string {
	return round === 0 ? "Before the first draft" : `Round ${round}`;
}

function manifestValue(value: unknown): string {
	if (Array.isArray(value)) {
		return value.map(manifestValue).join(", ");
	}
	if (typeof value === "object" && value !== null) {
		return Object.entries(value)
			.map(([key, inner]) => `${key} ${manifestValue(inner)}`)
			.join(", ");
	}
	return String(value);
}

const MANIFEST_NAMES: Readonly<Record<string, string>> = {
	model_ids: "Models",
	prompt_versions: "Prompt versions",
	retriever: "Retriever",
	max_rounds: "Round limit",
};

function TraceView({ trace }: { trace: Trace }) {
	const id = useId();
	const rounds = [...new Set(trace.steps.map((step) => step.round))].sort(
		(a, b) => a - b,
	);
	return (
		<Canvas title={TITLE}>
			<Text>
				Stopped because:{" "}
				{stoppedBecause(trace.stopped_because, trace.max_rounds)} · kept round{" "}
				{trace.kept_round} of {trace.rounds}
			</Text>
			{rounds.map((round) => (
				<Box key={round}>
					<CanvasHeading id={`${id}-round-${round}`}>
						{roundName(round)}
					</CanvasHeading>
					<chakra.ol
						aria-labelledby={`${id}-round-${round}`}
						listStyleType="none"
						p="0"
					>
						{trace.steps
							.filter((step) => step.round === round)
							.map((step, index) => (
								// biome-ignore lint/suspicious/noArrayIndexKey: steps keep the order they ran in
								<StepRow key={index} step={step} />
							))}
					</chakra.ol>
				</Box>
			))}
			<CanvasHeading>Totals</CanvasHeading>
			<Text>
				Model: {trace.model_id} · {plural(trace.totals.calls, "call")} ·{" "}
				{NUMBER.format(trace.totals.prompt_tokens)} tokens in,{" "}
				{NUMBER.format(trace.totals.completion_tokens)} out
			</Text>
			<CanvasHeading id={`${id}-used`}>What this used</CanvasHeading>
			<chakra.ul aria-labelledby={`${id}-used`} pl="5">
				{Object.entries(trace.manifest).map(([key, value]) => (
					<li key={key}>
						{MANIFEST_NAMES[key] ?? key.replaceAll("_", " ")}:{" "}
						{manifestValue(value)}
					</li>
				))}
			</chakra.ul>
		</Canvas>
	);
}

/** While the Brief stage runs: the steps the stream has reported so far. */
function LiveSteps() {
	const live = useLiveRun();
	const id = useId();
	const steps = live?.state.agentSteps ?? [];
	return (
		<Canvas title={TITLE}>
			<Text role="status">Still writing. Steps appear as they finish.</Text>
			<Heading as="h3" id={id} fontSize="sm" fontWeight="medium" mt="4" mb="2">
				Steps so far
			</Heading>
			<chakra.ol aria-labelledby={id} listStyleType="none" p="0">
				{steps.map((step, index) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: steps arrive in order and never move
					<Flex as="li" key={index} py="1" gap="2">
						{`${ROLES[step.role]} · round ${step.round} · ${STEP_STATUSES[step.status]}`}
					</Flex>
				))}
			</chakra.ol>
		</Canvas>
	);
}

export function TraceCanvas() {
	const api = useApi();
	const { review } = useReview();
	const live = useLiveRun();
	const search = useSearch({
		from: "/signed-in/p/$projectId/r/$reviewId/trace",
	});
	const brief = useQuery(briefQuery(api, review.id, search.run));
	const trace = useQuery({
		...traceQuery(api, brief.data?.id ?? ""),
		enabled: brief.data !== undefined,
	});
	const isWriting =
		live !== null &&
		!live.state.isFinished &&
		(live.state.currentStage === "answer" || live.state.agentSteps.length > 0);

	if (isApiError(brief.error, "not-found")) {
		return isWriting ? (
			<LiveSteps />
		) : (
			<Canvas title={TITLE}>
				<Text>
					No decision brief yet. It appears when a run's Brief stage finishes.
				</Text>
			</Canvas>
		);
	}
	const failed = brief.isError
		? brief.error
		: trace.isError
			? trace.error
			: null;
	if (failed !== null) {
		return (
			<Canvas title={TITLE}>
				<ErrorNotice error={failed} />
			</Canvas>
		);
	}
	if (trace.data === undefined) {
		return (
			<Canvas title={TITLE}>
				<Loading what="how the brief was made" />
			</Canvas>
		);
	}
	return <TraceView trace={trace.data} />;
}
