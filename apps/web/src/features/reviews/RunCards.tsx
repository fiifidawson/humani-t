/**
 * The run half of the chat (UI_UX_SPEC §5.3.5 to 5.3.7): the run strip and
 * its stage list, gates, and the screening summary. Live state comes from the
 * one stream in `LiveRunProvider`; runs before the latest fold to one line
 * from what the API stored, without opening a stream each.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	Code,
	chakra,
	Flex,
	Stack,
	Text,
	VisuallyHidden,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
	Check,
	ChevronDown,
	ChevronRight,
	Minus,
	Square,
	Triangle,
	TriangleAlert,
	X,
} from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { papersQuery } from "../../app/reviewQueries";
import { Dialog } from "../../ui/Dialog";
import { linkStyle } from "../../ui/LinkButton";
import { BriefCard } from "../briefs/BriefCard";
import { RunStrip } from "../runs/RunStrip";
import type { RunState, StageId } from "../runs/runEvents";
import { StageList } from "../runs/StageList";
import { ChatCard, ChatLine } from "./ChatCard";
import { clockTime, plural, TIER_ORDER, TIERS, type Tier } from "./format";
import { type LiveRun, useLiveRun } from "./liveRun";
import { type Run, stagesOf, useReview, useRunNumber } from "./reviewContext";

/** The stages to run again: from the one that stopped, else every one not done. */
export function stagesToRetry(run: Run, state: RunState): StageId[] {
	const requested = stagesOf(run);
	const stopped = state.stages.find(
		(s) => s.status === "failed" || s.status === "cancelled",
	);
	if (stopped !== undefined) {
		return requested.slice(requested.indexOf(stopped.stage));
	}
	const done = new Set(
		state.stages.filter((s) => s.status === "done").map((s) => s.stage),
	);
	return requested.filter((stage) => !done.has(stage));
}

export function CancelRunButton({ run }: { run: Run }) {
	const { actions } = useReview();
	const [isAsking, setAsking] = useState(false);
	return (
		<>
			<Button size="xs" variant="outline" onClick={() => setAsking(true)}>
				Cancel
			</Button>
			<Dialog
				isOpen={isAsking}
				title="Stop this run?"
				onClose={() => setAsking(false)}
			>
				<Stack gap="5">
					<Text>Finished stages are kept, and you can run the rest later.</Text>
					<Flex gap="3" wrap="wrap">
						<Button
							colorPalette="danger"
							disabled={actions.cancel.isPending}
							onClick={() =>
								actions.cancel.mutate(run.id, {
									onSettled: () => setAsking(false),
								})
							}
						>
							Stop the run
						</Button>
						<Button variant="outline" onClick={() => setAsking(false)}>
							Keep running
						</Button>
					</Flex>
				</Stack>
			</Dialog>
		</>
	);
}

export function RetryButton({ live }: { live: LiveRun }) {
	const { actions } = useReview();
	return (
		<Button
			size="xs"
			variant="outline"
			disabled={actions.startRun.isPending}
			onClick={() =>
				actions.startRun.mutate(stagesToRetry(live.run, live.state))
			}
		>
			Try again from this stage
		</Button>
	);
}

function RunLinks({ run }: { run: Run }) {
	const { project, review } = useReview();
	return (
		<ChakraLink asChild css={linkStyle} fontSize="sm">
			<Link
				to="/p/$projectId/r/$reviewId/run/$runId"
				params={{ projectId: project.id, reviewId: review.id, runId: run.id }}
			>
				Open run details
			</Link>
		</ChakraLink>
	);
}

/** The latest run: one line that updates in place, opening to its stages (§5.3.5). */
export function RunCard({ live }: { live: LiveRun }) {
	const { canManage, actions } = useReview();
	const { run, state, connection } = live;
	const number = useRunNumber(run.id);
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const isFinished = state.isFinished || run.outcome !== null;
	const canRetry =
		canManage && (state.status === "failed" || state.status === "cancelled");

	return (
		<Box as="article" aria-labelledby={`${id}-title`}>
			<RunStrip
				state={state}
				connection={connection}
				title={{ id: `${id}-title`, text: `Run ${number}` }}
				action={
					<chakra.button
						type="button"
						display="inline-flex"
						alignItems="center"
						justifyContent="center"
						boxSize="8"
						flex="none"
						rounded="card"
						bg="transparent"
						border="0"
						color="fg.muted"
						cursor="pointer"
						_hover={{ bg: "bg.muted" }}
						aria-expanded={isOpen}
						aria-controls={`${id}-stages`}
						onClick={() => setOpen(!isOpen)}
					>
						{isOpen ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
						<VisuallyHidden>Show stages</VisuallyHidden>
					</chakra.button>
				}
			>
				{isOpen ? (
					<Stack
						id={`${id}-stages`}
						gap="3"
						mt="3"
						pt="3"
						borderTopWidth="1px"
						borderColor="border"
					>
						<Flex align="center" gap="3" wrap="wrap" fontSize="sm">
							<Text flex="1" color="fg.muted">
								{state.startedAt === null
									? "Not started yet"
									: `Started ${clockTime(state.startedAt)}`}
							</Text>
							{canManage && !isFinished ? <CancelRunButton run={run} /> : null}
						</Flex>
						<Box>
							<VisuallyHidden id={`${id}-list`}>Stages</VisuallyHidden>
							<StageList
								stages={stagesOf(run)}
								state={state}
								labelId={`${id}-list`}
							/>
						</Box>
						{state.latestItem === null ? null : (
							<Text fontSize="sm" color="fg.muted" overflowWrap="anywhere">
								Latest: {state.latestItem}
							</Text>
						)}
						<Flex gap="3" align="center" wrap="wrap">
							{canRetry ? <RetryButton live={live} /> : null}
							<RunLinks run={run} />
						</Flex>
						{actions.cancel.isError ? (
							<Text role="alert" color="fg.error" fontSize="sm">
								The run couldn't be cancelled. It may have finished already.
							</Text>
						) : null}
					</Stack>
				) : null}
			</RunStrip>
		</Box>
	);
}

const OUTCOME_WORDS: Readonly<Record<Run["state"], [ReactNode, string]>> = {
	queued: [<Minus key="q" size={16} />, "Queued"],
	running: [<Minus key="r" size={16} />, "Running"],
	waiting: [<Minus key="w" size={16} />, "Waiting for you"],
	done: [<Check key="d" size={16} />, "Finished"],
	failed: [<X key="f" size={16} />, "Stopped"],
	cancelled: [<Square key="c" size={16} />, "Cancelled"],
};

/** A run before the latest, folded to its outcome (§5.3.5 "the old one folds to L0"). */
export function PastRunLine({ run }: { run: Run }) {
	const { project, review } = useReview();
	const number = useRunNumber(run.id);
	const [icon, word] = OUTCOME_WORDS[run.state];
	return (
		<ChatLine
			icon={icon}
			actions={
				<ChakraLink asChild css={linkStyle} fontSize="sm">
					<Link
						to="/p/$projectId/r/$reviewId/run/$runId"
						params={{
							projectId: project.id,
							reviewId: review.id,
							runId: run.id,
						}}
						aria-label={`Open run ${number}`}
					>
						Open
					</Link>
				</ChakraLink>
			}
		>
			<span>{`Run ${number} · ${word}`}</span>
		</ChatLine>
	);
}

/** A run waiting for a person (§5.3.6). Who may decide is the API's rule; this hides what the role cannot do. */
export function GateCard({ live }: { live: LiveRun }) {
	const { project, canManage, actions } = useReview();
	const { gate, gatePayload } = live.state;
	if (gate === null) {
		return null;
	}
	const { decide } = actions;
	const isSent = decide.isSuccess && decide.variables?.gateId === gate.gateId;
	const send = (decision: "approve" | "reject") =>
		decide.mutate({ gateId: gate.gateId, decision });

	if (gate.kind === "privacy_review") {
		return (
			<ChatCard title="Needs you · Privacy review" tone="warning">
				<Text>
					Uploaded documents need a masking check before any model reads them.
					The run continues when an owner approves.
				</Text>
				<Text color="fg.muted">
					{project.role === "owner"
						? "The masking review isn't available in the browser yet."
						: "Only owners can do this."}
				</Text>
			</ChatCard>
		);
	}

	const rendered = gatePayload?.["rendered"];
	return (
		<ChatCard
			title="Needs you · Confirm the search terms"
			tone="warning"
			actions={
				canManage && !isSent ? (
					<>
						<Button
							size="sm"
							disabled={decide.isPending}
							onClick={() => send("approve")}
						>
							Confirm and continue
						</Button>
						<Button
							size="sm"
							variant="outline"
							disabled={decide.isPending}
							onClick={() => send("reject")}
						>
							Reject and stop the run
						</Button>
					</>
				) : undefined
			}
		>
			<Text>
				The run was started before the search terms were confirmed. It continues
				when you confirm them.
			</Text>
			{typeof rendered === "string" ? (
				<Code
					display="block"
					whiteSpace="pre-wrap"
					p="2"
					overflowWrap="anywhere"
				>
					{rendered}
				</Code>
			) : null}
			{canManage ? null : (
				<Text color="fg.muted">Only owners and reviewers can do this.</Text>
			)}
			{isSent ? (
				<Text role="status">Sent. The run continues in a moment.</Text>
			) : null}
			{decide.isError ? (
				<Text role="alert" color="fg.error">
					{isApiError(decide.error, "conflict")
						? "Someone already decided. The run will show what happens next."
						: "The decision couldn't be sent. Try again."}
				</Text>
			) : null}
		</ChatCard>
	);
}

const TIER_ICONS: Readonly<Record<Tier | "unscreened", ReactNode>> = {
	highly_relevant: <Triangle size={14} fill="currentColor" />,
	relevant: <Triangle size={14} fill="currentColor" />,
	possibly_relevant: <Triangle size={14} />,
	uncertain: <TriangleAlert size={14} />,
	excluded: <X size={14} />,
	unscreened: <Minus size={14} />,
};

/** A screening label as the word and its icon (§10.2 `LabelBadge`); colour only repeats them. */
export function LabelBadge({ tier }: { tier: Tier | "unscreened" }) {
	return (
		<Flex
			as="span"
			display="inline-flex"
			align="center"
			gap="1.5"
			color={TIERS[tier].tone}
		>
			{TIER_ICONS[tier]}
			<Text as="span" color="fg">
				{TIERS[tier].word}
			</Text>
		</Flex>
	);
}

/** Posted once Screen finishes (§5.3.7): counts per label, from the API's own tally. */
export function ScreeningCard({ live }: { live: LiveRun }) {
	const api = useApi();
	const { project, review } = useReview();
	const papers = useQuery({
		...papersQuery(api, review.id, {
			runId: live.run.id,
			label: null,
			page: 1,
		}),
		enabled: live.state.summaries.screen !== undefined,
	});
	if (live.state.summaries.screen === undefined || papers.data === undefined) {
		return null;
	}
	const { counts } = papers.data;
	const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
	return (
		<ChatCard
			title={`Screening · ${plural(total, "paper")}`}
			actions={
				<ChakraLink asChild css={linkStyle} fontSize="sm">
					<Link
						to="/p/$projectId/r/$reviewId/papers"
						params={{ projectId: project.id, reviewId: review.id }}
						search={{ run: live.run.id }}
					>
						Open papers
					</Link>
				</ChakraLink>
			}
		>
			<chakra.ul
				listStyleType="none"
				p="0"
				display="flex"
				flexWrap="wrap"
				gap="3"
			>
				{TIER_ORDER.filter((tier) => (counts[tier] ?? 0) > 0).map((tier) => (
					<Flex as="li" key={tier} align="center" gap="1.5">
						<Text
							as="span"
							fontWeight="semibold"
							fontVariantNumeric="tabular-nums"
						>
							{counts[tier]}
						</Text>{" "}
						<LabelBadge tier={tier} />
					</Flex>
				))}
			</chakra.ul>
		</ChatCard>
	);
}

/** The latest run's cards in order: the strip, a gate if one is open, the screening summary. */
export function LatestRunCards() {
	const live = useLiveRun();
	if (live === null) {
		return null;
	}
	return (
		<>
			<RunCard live={live} />
			<GateCard live={live} />
			<ScreeningCard live={live} />
			<BriefCard live={live} />
		</>
	);
}
