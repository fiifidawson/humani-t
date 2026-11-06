/**
 * The run strip's one-line form (UI_UX_SPEC §5.3.5, level L0).
 *
 * One line that updates in place for the whole run. The words come from the
 * state alone. The caller may put a toggle at the end of the line and the
 * expanded form (L1, features/reviews/RunCards) inside the same frame.
 */

import { Box, Flex, Heading, Text, VisuallyHidden } from "@chakra-ui/react";
import {
	Check,
	Circle,
	LoaderCircle,
	Pause,
	Square,
	WifiOff,
	X,
} from "lucide-react";
import type { ReactNode } from "react";
import type { RunState, RunStatus } from "./runEvents";
import { activityOf, GATES } from "./stages";
import type { Connection } from "./useRunEvents";

/** Every status has a word and an icon; colour only repeats them (UI_UX_SPEC §9.2). */
const STATUS: Readonly<
	Record<RunStatus, { icon: ReactNode; word: string; tone: string }>
> = {
	idle: { icon: <Circle size={16} />, word: "Starting", tone: "fg.muted" },
	running: {
		icon: <LoaderCircle size={16} />,
		word: "Running",
		tone: "brand.fg",
	},
	waiting: {
		icon: <Pause size={16} />,
		word: "Waiting for you",
		tone: "caveat.fg",
	},
	done: { icon: <Check size={16} />, word: "Finished", tone: "brand.fg" },
	failed: { icon: <X size={16} />, word: "Stopped", tone: "fg.error" },
	cancelled: {
		icon: <Square size={16} />,
		word: "Cancelled",
		tone: "fg.muted",
	},
};

function minutes(seconds: number | null): string {
	return seconds === null
		? ""
		: ` · ${Math.max(1, Math.round(seconds / 60))} min`;
}

function line(state: RunState): string {
	const activity = activityOf(state.currentStage);
	const stage = state.stages.find((s) => s.stage === state.currentStage);
	switch (state.status) {
		case "idle":
			return "Starting the run";
		case "running":
			return stage?.done != null && stage.total != null
				? `${activity} · ${stage.done} of ${stage.total}`
				: activity;
		case "waiting":
			return `Waiting for you · ${state.gate === null ? "A decision is needed" : GATES[state.gate.kind]}`;
		case "done":
			return `Run finished${minutes(state.seconds)}`;
		case "failed":
			return `Run stopped at ${activity}${state.error === null ? "" : ` · ${state.error}`}`;
		case "cancelled":
			return `Run cancelled at ${activity} · finished stages are kept`;
	}
}

/** What a screen reader hears: stage changes and the end, never each count. */
function announcement(state: RunState): string {
	const activity = activityOf(state.currentStage);
	switch (state.status) {
		case "idle":
			return "";
		case "running":
			return `${activity} started.`;
		case "waiting":
			return `Waiting for you: ${(state.gate === null ? "a decision" : GATES[state.gate.kind]).toLowerCase()}.`;
		case "done":
			return "Run finished.";
		case "failed":
			return `Run stopped at ${activity}.`;
		case "cancelled":
			return `Run cancelled at ${activity}.`;
	}
}

interface RunStripProps {
	state: RunState;
	connection: Connection;
	/** A heading above the line ("Run 2"), with its id for the card's label. */
	title?: { id: string; text: string };
	/** Something at the end of the line, such as the toggle that opens L1. */
	action?: ReactNode;
	/** The expanded form (L1), inside the same frame. */
	children?: ReactNode;
}

export function RunStrip({
	state,
	connection,
	title,
	action,
	children,
}: RunStripProps) {
	const { icon, word, tone } = STATUS[state.status];
	const isWaiting = state.status === "waiting";
	return (
		<Box
			px="4"
			py="3"
			rounded="card"
			borderWidth="1px"
			borderColor={isWaiting ? "border.warning" : "border"}
			bg={isWaiting ? "caveat.subtle" : "bg.panel"}
		>
			{title === undefined ? null : (
				<Heading
					as="h2"
					id={title.id}
					fontFamily="body"
					fontSize="xs"
					fontWeight="normal"
					textTransform="uppercase"
					letterSpacing="0.08em"
					color="fg.muted"
					mb="1"
				>
					{title.text}
				</Heading>
			)}
			<Flex align="center" gap="2.5" fontSize="sm">
				<Flex as="p" align="center" gap="2.5" flex="1" minW="0">
					<Box
						as="span"
						display="inline-flex"
						color={tone}
						css={
							state.status === "running"
								? { "& svg": { animation: "spin 1.1s linear infinite" } }
								: undefined
						}
					>
						{icon}
						<VisuallyHidden>{word}</VisuallyHidden>
					</Box>
					<Text
						as="span"
						fontWeight={isWaiting ? "semibold" : "normal"}
						minW="0"
					>
						{line(state)}
					</Text>
				</Flex>
				{action}
			</Flex>
			{connection === "reconnecting" ? (
				<Flex
					as="p"
					align="center"
					gap="2"
					mt="2"
					fontSize="xs"
					color="fg.muted"
				>
					<WifiOff size={14} />
					Reconnecting…
				</Flex>
			) : null}
			{children}
			<VisuallyHidden as="p" role="status">
				{announcement(state)}
			</VisuallyHidden>
		</Box>
	);
}
