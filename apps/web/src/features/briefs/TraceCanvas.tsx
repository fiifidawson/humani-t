/**
 * How the brief was made: the agents' steps round by round (AgentTimeline), the
 * totals, the instructions the Writer had and what the run used.
 *
 * While the Brief stage runs there is no trace yet, so steps come from the run's stream.
 */

import { Box, Code, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { Fragment, type ReactNode, useId } from "react";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { briefQuery, traceQuery } from "../../app/briefQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Canvas, CanvasHeading } from "../reviews/Canvas";
import { plural } from "../reviews/format";
import { useLiveRun } from "../reviews/liveRun";
import { MaskedText } from "../reviews/MaskedText";
import { useReview } from "../reviews/reviewContext";
import { AgentTimeline } from "./AgentTimeline";
import { ROLES, stoppedBecause } from "./words";

type Schemas = components["schemas"];
type Trace = Schemas["TraceOut"];

const TITLE = "How this brief was made";
const NUMBER = new Intl.NumberFormat("en-GB");

/** A value the run recorded: identifiers in code type, so they stand apart from the names. */
function Recorded({ value }: { value: unknown }) {
	if (typeof value === "number" || typeof value === "boolean") {
		return <>{String(value)}</>;
	}
	if (Array.isArray(value)) {
		return (
			<Stack gap="1">
				{value.map((inner, index) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: the run's order, never reordered
					<Recorded key={index} value={inner} />
				))}
			</Stack>
		);
	}
	if (typeof value === "object" && value !== null) {
		return (
			<Stack gap="1">
				{Object.entries(value).map(([key, inner]) => (
					<Flex key={key} gap="2" align="baseline" wrap="wrap">
						<Text as="span" color="fg.muted" minW="16">
							{key in ROLES ? ROLES[key as keyof typeof ROLES] : key}
						</Text>
						<Recorded value={inner} />
					</Flex>
				))}
			</Stack>
		);
	}
	return <Code fontSize="xs">{String(value)}</Code>;
}

const MANIFEST_NAMES: Readonly<Record<string, string>> = {
	model_ids: "Models",
	prompt_versions: "Prompt versions",
	retriever: "Retriever",
	max_rounds: "Round limit",
	instructions_version: "Instructions version",
};

function UsedName({ children }: { children: ReactNode }) {
	return (
		<chakra.dt color="fg.muted" fontWeight="medium">
			{children}
		</chakra.dt>
	);
}

/** The project's writing instructions as the Writer got them (ADR 0015). */
function InstructionsUsed({ trace }: { trace: Trace }) {
	const id = useId();
	const sent = trace.instructions;
	return (
		<Box as="section" aria-labelledby={id}>
			<CanvasHeading id={id}>Instructions from the project team</CanvasHeading>
			{sent === null ? (
				<Text>None. The Writer had only the built-in instructions.</Text>
			) : (
				<Stack gap="1">
					<Text color="fg.muted">
						Version {sent.version}, as the Writer received them, masked where
						masking applies.
					</Text>
					<MaskedText text={sent.text} />
				</Stack>
			)}
		</Box>
	);
}

function TraceView({ trace }: { trace: Trace }) {
	const id = useId();
	return (
		<Canvas title={TITLE}>
			<Text mb="3">
				Stopped because:{" "}
				{stoppedBecause(trace.stopped_because, trace.max_rounds)} · kept round{" "}
				{trace.kept_round} of {trace.rounds}
			</Text>
			<CanvasHeading>Steps</CanvasHeading>
			<AgentTimeline steps={trace.steps} isLive={false} />
			<CanvasHeading>Totals</CanvasHeading>
			<Text>
				Model: {trace.model_id} · {plural(trace.totals.calls, "call")} ·{" "}
				{NUMBER.format(trace.totals.prompt_tokens)} tokens in,{" "}
				{NUMBER.format(trace.totals.completion_tokens)} out
			</Text>
			<InstructionsUsed trace={trace} />
			<CanvasHeading id={`${id}-used`}>What this used</CanvasHeading>
			<chakra.dl
				aria-labelledby={`${id}-used`}
				display="grid"
				gridTemplateColumns={{ base: "1fr", sm: "max-content 1fr" }}
				columnGap="6"
				rowGap={{ base: "1", sm: "3" }}
				fontSize="sm"
				css={{ "& dd": { margin: "0", mb: { base: "2", sm: "0" } } }}
			>
				{trace.is_masked === null ? null : (
					<>
						<UsedName>Masking</UsedName>
						<dd>
							{trace.is_masked ? "On" : "Off for this run (the owner's choice)"}
						</dd>
					</>
				)}
				{Object.entries(trace.manifest).map(([key, value]) => (
					<Fragment key={key}>
						<UsedName>
							{MANIFEST_NAMES[key] ?? key.replaceAll("_", " ")}
						</UsedName>
						<dd>
							<Recorded value={value} />
						</dd>
					</Fragment>
				))}
			</chakra.dl>
		</Canvas>
	);
}

/** While the Brief stage runs: the loop as the stream reports it. */
function LiveSteps() {
	const live = useLiveRun();
	return (
		<Canvas title={TITLE}>
			<Text role="status" mb="3">
				Still writing. Steps appear as they finish.
			</Text>
			<CanvasHeading>Steps</CanvasHeading>
			<AgentTimeline steps={live?.state.agentSteps ?? []} isLive />
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
