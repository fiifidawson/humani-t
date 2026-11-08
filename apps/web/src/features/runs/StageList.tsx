/**
 * A run's stages in order, each with its state as a word and an icon
 * (UI_UX_SPEC §5.3.5 L1, §6.7). Stages the run has not reached yet are listed
 * too, so the length of the run is visible from the start.
 */

import { chakra, Flex, Text } from "@chakra-ui/react";
import { Check, Hand, LoaderCircle, Square, X } from "lucide-react";
import type { ReactNode } from "react";
import { duration } from "../reviews/format";
import type { RunState, StageId, StageProgress } from "./runEvents";
import { stageName } from "./stages";

interface Look {
	icon: ReactNode;
	word: string;
	/** The marker's look: filled when finished, a ring while working, hollow before. */
	marker: "done" | "active" | "waiting" | "failed" | "cancelled" | "upcoming";
}

function lookOf(progress: StageProgress | undefined, isWaiting: boolean): Look {
	if (progress === undefined) {
		return { icon: null, word: "Not started", marker: "upcoming" };
	}
	switch (progress.status) {
		case "done":
			return {
				icon: <Check size={14} strokeWidth={3} />,
				word: "Done",
				marker: "done",
			};
		case "failed":
			return {
				icon: <X size={14} strokeWidth={3} />,
				word: "Stopped",
				marker: "failed",
			};
		case "cancelled":
			return {
				icon: <Square size={10} />,
				word: "Cancelled",
				marker: "cancelled",
			};
		case "running":
			return isWaiting
				? {
						icon: <Hand size={14} />,
						word: "Waiting for you",
						marker: "waiting",
					}
				: {
						icon: <LoaderCircle size={14} className="stage-spin" />,
						word: "Running",
						marker: "active",
					};
	}
}

const MARKERS: Readonly<Record<Look["marker"], Record<string, string>>> = {
	done: {
		bg: "brand.solid",
		color: "brand.contrast",
		borderColor: "brand.solid",
	},
	active: { bg: "bg.panel", color: "brand.fg", borderColor: "brand.solid" },
	waiting: {
		bg: "brand.solid",
		color: "brand.contrast",
		borderColor: "brand.solid",
	},
	failed: {
		bg: "danger.solid",
		color: "danger.contrast",
		borderColor: "danger.solid",
	},
	cancelled: {
		bg: "bg.muted",
		color: "fg.muted",
		borderColor: "border.emphasized",
	},
	upcoming: {
		bg: "bg.panel",
		color: "fg.muted",
		borderColor: "border.emphasized",
	},
};

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

/**
 * A vertical stepper: a marker per stage joined by a line, the line blue as far
 * as the run has got. The current stage carries `aria-current="step"`; the
 * marker only repeats what the words say.
 */
export function StageList({ stages, state, labelId }: StageListProps) {
	return (
		<chakra.ol
			aria-labelledby={labelId}
			listStyleType="none"
			p="0"
			m="0"
			css={{
				"@keyframes stage-spin": { to: { transform: "rotate(360deg)" } },
				"& .stage-spin": { animation: "stage-spin 1s linear infinite" },
			}}
		>
			{stages.map((stage, index) => {
				const progress = state.stages.find((s) => s.stage === stage);
				const isWaiting =
					state.status === "waiting" && state.currentStage === stage;
				const look = lookOf(progress, isWaiting);
				const isCurrent = look.marker === "active" || look.marker === "waiting";
				const isLast = index === stages.length - 1;
				return (
					<Flex
						as="li"
						key={stage}
						aria-current={isCurrent ? "step" : undefined}
						gap="3"
						position="relative"
						pb={isLast ? "0" : "3"}
						fontSize="sm"
						css={
							isLast
								? undefined
								: {
										"&::before": {
											content: '""',
											position: "absolute",
											left: "11px",
											top: "24px",
											bottom: "0",
											width: "2px",
											bg: look.marker === "done" ? "brand.solid" : "border",
										},
									}
						}
					>
						<Flex
							as="span"
							aria-hidden="true"
							flex="none"
							boxSize="6"
							align="center"
							justify="center"
							rounded="full"
							borderWidth="2px"
							fontSize="xs"
							fontWeight="semibold"
							{...MARKERS[look.marker]}
						>
							{look.icon ?? index + 1}
						</Flex>
						<Text as="span" title={`Stage id: ${stage}`} pt="0.5">
							<Text as="span" fontWeight={isCurrent ? "semibold" : "medium"}>
								{stageName(stage)}
							</Text>
							<Text as="span" color="fg.muted">
								{" "}
								· {look.word}
								{detail(progress)}
							</Text>
						</Text>
					</Flex>
				);
			})}
		</chakra.ol>
	);
}
