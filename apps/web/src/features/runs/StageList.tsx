/**
 * A run's stages in order, each with its state as a word and an icon
 * (UI_UX_SPEC §5.3.5 L1, §6.7). Stages the run has not reached yet are listed
 * too, so the length of the run is visible from the start.
 */

import { Box, chakra, Flex, Text } from "@chakra-ui/react";
import { Check, Circle, LoaderCircle, Pause, Square, X } from "lucide-react";
import type { ReactNode } from "react";
import { duration } from "../reviews/format";
import type { RunState, StageId, StageProgress } from "./runEvents";
import { stageName } from "./stages";

interface Look {
	icon: ReactNode;
	word: string;
	tone: string;
}

function lookOf(progress: StageProgress | undefined, isWaiting: boolean): Look {
	if (progress === undefined) {
		return {
			icon: <Circle size={16} />,
			word: "Not started",
			tone: "fg.muted",
		};
	}
	switch (progress.status) {
		case "done":
			return { icon: <Check size={16} />, word: "Done", tone: "brand.fg" };
		case "failed":
			return { icon: <X size={16} />, word: "Stopped", tone: "fg.error" };
		case "cancelled":
			return {
				icon: <Square size={16} />,
				word: "Cancelled",
				tone: "fg.muted",
			};
		case "running":
			return isWaiting
				? {
						icon: <Pause size={16} />,
						word: "Waiting for you",
						tone: "caveat.fg",
					}
				: {
						icon: <LoaderCircle size={16} />,
						word: "Running",
						tone: "brand.fg",
					};
	}
}

function detail(progress: StageProgress | undefined): string {
	if (progress === undefined) {
		return "";
	}
	if (progress.status === "done" && progress.seconds !== null) {
		return ` · ${duration(progress.seconds)}`;
	}
	if (progress.done !== null && progress.total !== null) {
		return ` · ${progress.done} of ${progress.total}`;
	}
	return "";
}

interface StageListProps {
	/** The stages this run was asked for, in pipeline order. */
	stages: readonly StageId[];
	state: RunState;
	labelId: string;
}

export function StageList({ stages, state, labelId }: StageListProps) {
	return (
		<chakra.ol aria-labelledby={labelId} listStyleType="none" p="0" m="0">
			{stages.map((stage) => {
				const progress = state.stages.find((s) => s.stage === stage);
				const isWaiting =
					state.status === "waiting" && state.currentStage === stage;
				const { icon, word, tone } = lookOf(progress, isWaiting);
				return (
					<Flex
						as="li"
						key={stage}
						align="center"
						gap="2.5"
						py="1"
						fontSize="sm"
					>
						<Box as="span" display="inline-flex" color={tone} flex="none">
							{icon}
						</Box>
						<Text as="span" title={`Stage id: ${stage}`}>
							{stageName(stage)} · {word}
							{detail(progress)}
						</Text>
					</Flex>
				);
			})}
		</chakra.ol>
	);
}
