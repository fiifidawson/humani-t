/**
 * Stage words people know (UI_UX_SPEC §5.3.5): a name for lists and an
 * activity for the strip's one line. The internal id goes in a tooltip.
 */

import type { StageId } from "./runEvents";

const STAGES: Readonly<Record<StageId, { name: string; activity: string }>> = {
	queries: { name: "Queries", activity: "Making queries" },
	discover: { name: "Discover", activity: "Discovering" },
	ingest: { name: "Ingest", activity: "Reading papers" },
	mask: { name: "Mask", activity: "Masking" },
	screen: { name: "Screen", activity: "Screening" },
	export: { name: "Export", activity: "Exporting" },
	index: { name: "Index", activity: "Preparing for questions" },
	answer: { name: "Brief", activity: "Writing the brief" },
};

export const stageName = (stage: StageId): string => STAGES[stage].name;

export function activityOf(stage: StageId | null): string {
	return stage === null ? "Starting" : STAGES[stage].activity;
}

/** What a gate is called in the UI ("gate" itself is internal, §14.2). */
export const GATES = {
	privacy_review: "Privacy review",
	vocabulary: "Confirm the search terms",
} as const;
