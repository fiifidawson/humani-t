/**
 * The run canvas (UI_UX_SPEC §6.7): every stage, the current stage's
 * progress, each paper as it is screened, and the brief's agent steps. The
 * latest run reuses the page's one stream; an older run opened from its line
 * replays its own stored events once and closes.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Text,
} from "@chakra-ui/react";
import { Link, useParams } from "@tanstack/react-router";
import { WifiOff } from "lucide-react";
import { useId, useState } from "react";
import { linkStyle } from "../../ui/LinkButton";
import { StageList } from "../runs/StageList";
import { stageName } from "../runs/stages";
import { useRunEvents } from "../runs/useRunEvents";
import { Canvas, CanvasHeading } from "./Canvas";
import { clockTime, readScreenedItem, type Tier } from "./format";
import { type LiveRun, runEventsUrl, useLiveRun } from "./liveRun";
import { MaskedText } from "./MaskedText";
import { CancelRunButton, LabelBadge, RetryButton } from "./RunCards";
import { type Run, stagesOf, useReview, useRunNumber } from "./reviewContext";

const FILTERS: readonly {
	name: string;
	tiers: readonly (Tier | null)[] | null;
}[] = [
	{ name: "All", tiers: null },
	{
		name: "Relevant",
		tiers: ["highly_relevant", "relevant", "possibly_relevant"],
	},
	{ name: "Excluded", tiers: ["excluded"] },
	{ name: "Failed", tiers: ["uncertain", null] },
];

const ROLES = {
	planner: "Planner",
	retriever: "Retriever",
	writer: "Writer",
	checks: "Checks",
	critic: "Critic",
} as const;

const STEP_STATUS = {
	ok: "done",
	refused: "refused",
	invalid: "unusable answer",
	error: "error",
} as const;

const Progress = chakra("progress", {
	base: {
		display: "block",
		w: "full",
		h: "2",
		appearance: "none",
		border: "0",
		rounded: "pill",
		overflow: "hidden",
		bg: "bg.muted",
		color: "brand.solid",
		"&::-webkit-progress-bar": { bg: "bg.muted" },
		"&::-webkit-progress-value": { bg: "brand.solid" },
		"&::-moz-progress-bar": { bg: "brand.solid" },
	},
});

function ScreenedPapers({ live }: { live: LiveRun }) {
	const id = useId();
	const [filter, setFilter] = useState(0);
	const tiers = FILTERS[filter]?.tiers ?? null;
	const items = live.state.items
		.filter((entry) => entry.stage === "screen")
		.map((entry) => ({ id: entry.id, ...readScreenedItem(entry.item) }))
		.filter((item) => tiers === null || tiers.includes(item.tier));

	return (
		<>
			<CanvasHeading id={id}>Screened papers</CanvasHeading>
			<Flex role="group" aria-label="Show" gap="2" wrap="wrap" mb="3">
				{FILTERS.map((option, index) => (
					<Button
						key={option.name}
						size="xs"
						variant={filter === index ? "solid" : "outline"}
						aria-pressed={filter === index}
						onClick={() => setFilter(index)}
					>
						{option.name}
					</Button>
				))}
			</Flex>
			{items.length === 0 ? (
				<Text color="fg.muted">No papers here yet.</Text>
			) : (
				<chakra.ol aria-labelledby={id} listStyleType="none" p="0">
					{items.map((item) => (
						<Box
							as="li"
							key={item.id}
							py="2"
							borderBottomWidth="1px"
							borderColor="border"
						>
							<Flex gap="3" align="baseline" wrap="wrap">
								<Box flex="none" minW="32">
									{item.tier === null ? (
										<Text as="span">{item.label}</Text>
									) : (
										<LabelBadge tier={item.tier} />
									)}
								</Box>
								<Box flex="1" minW="0">
									<MaskedText text={item.title} />
									{item.reason === null ? null : (
										<Text color="fg.muted">{item.reason}</Text>
									)}
								</Box>
							</Flex>
						</Box>
					))}
				</chakra.ol>
			)}
		</>
	);
}

function RunView({ live }: { live: LiveRun }) {
	const { project, review, plan, canManage } = useReview();
	const { run, state, connection } = live;
	const number = useRunNumber(run.id);
	const id = useId();
	const progress = state.stages.find((s) => s.stage === state.currentStage);
	const isFinished = state.isFinished || run.outcome !== null;
	const hasScreened = state.items.some((item) => item.stage === "screen");

	return (
		<Canvas title={`Run ${number}`}>
			<Flex gap="3" align="center" wrap="wrap">
				<Text flex="1" color="fg.muted">
					{[
						plan.mode === null ? null : plan.mode === "deep" ? "Deep" : "Quick",
						state.startedAt === null
							? "Not started yet"
							: `Started ${clockTime(state.startedAt)}`,
					]
						.filter((part) => part !== null)
						.join(" · ")}
				</Text>
				{canManage && !isFinished ? <CancelRunButton run={run} /> : null}
			</Flex>
			{connection === "reconnecting" ? (
				<Text
					mt="2"
					display="flex"
					gap="2"
					alignItems="center"
					color="fg.muted"
				>
					<WifiOff size={14} />
					Reconnecting…
				</Text>
			) : null}
			{state.status === "failed" || state.status === "cancelled" ? (
				<Flex mt="3" gap="3" align="center" wrap="wrap">
					<Text
						flex="1"
						color={state.status === "failed" ? "fg.error" : "fg.muted"}
					>
						{state.status === "failed"
							? (state.error ?? "The run stopped.")
							: "The run was cancelled. Finished stages are kept."}
					</Text>
					{canManage ? <RetryButton live={live} /> : null}
				</Flex>
			) : null}

			<CanvasHeading id={`${id}-stages`}>Stages</CanvasHeading>
			<StageList
				stages={stagesOf(run)}
				state={state}
				labelId={`${id}-stages`}
			/>

			{progress?.done == null || progress.total == null || isFinished ? null : (
				<Box mt="4">
					<Text id={`${id}-progress`} mb="1.5">
						{stageName(progress.stage)} · {progress.done} of {progress.total}
					</Text>
					<Progress
						aria-labelledby={`${id}-progress`}
						value={progress.done}
						max={progress.total}
					/>
				</Box>
			)}

			{hasScreened || state.stages.some((s) => s.stage === "screen") ? (
				<ScreenedPapers live={live} />
			) : null}
			{state.summaries.screen === undefined ? null : (
				<ChakraLink asChild css={linkStyle} mt="3" display="inline-block">
					<Link
						to="/p/$projectId/r/$reviewId/papers"
						params={{ projectId: project.id, reviewId: review.id }}
						search={{ run: run.id }}
					>
						Open papers
					</Link>
				</ChakraLink>
			)}

			{state.agentSteps.length === 0 ? null : (
				<>
					<CanvasHeading id={`${id}-steps`}>Writing the brief</CanvasHeading>
					<chakra.ol aria-labelledby={`${id}-steps`} pl="5">
						{state.agentSteps.map((step, index) => (
							// biome-ignore lint/suspicious/noArrayIndexKey: steps arrive in order and never move
							<li key={index}>
								{ROLES[step.role]} · round {step.round} ·{" "}
								{STEP_STATUS[step.status]}
							</li>
						))}
					</chakra.ol>
				</>
			)}
		</Canvas>
	);
}

/** An older run: its own stream, which replays what was stored and closes. */
function PastRun({ run }: { run: Run }) {
	const { state, connection } = useRunEvents(runEventsUrl(run.id));
	return <RunView live={{ run, state, connection }} />;
}

export function RunCanvas() {
	const { runId } = useParams({
		from: "/signed-in/p/$projectId/r/$reviewId/run/$runId",
	});
	const { runs } = useReview();
	const live = useLiveRun();
	const run = runs.find((candidate) => candidate.id === runId);

	if (run === undefined) {
		return (
			<Canvas title="Run not found">
				<Text>This run isn't part of this review.</Text>
			</Canvas>
		);
	}
	return live?.run.id === run.id ? (
		<RunView live={live} />
	) : (
		<PastRun key={run.id} run={run} />
	);
}
