/**
 * The brief's fixed wording (UI_UX_SPEC §8.1, §8.3, §6.9 to 6.11), written
 * once so every screen says the same thing. Keyed by the API's enums, so a
 * new value fails the type check here rather than showing a raw code.
 */

import type { components } from "../../api/schema.gen";

type Schemas = components["schemas"];
export type Strength = Schemas["EvidenceStrength"];
export type StopReason = Schemas["StopReason"];
export type Role = Schemas["AgentRole"];
export type StepStatus = Schemas["StepStatus"];

/** §8.1: the word, how many of three dots are filled, the tone, the hover text. */
export const STRENGTHS: Readonly<
	Record<
		Strength,
		{ word: string; dots: number; tone: string; meaning: string }
	>
> = {
	strong: {
		word: "Strong evidence",
		dots: 3,
		tone: "benefit.fg",
		meaning:
			"Several good studies agree. New studies are unlikely to change this much",
	},
	moderate: {
		word: "Moderate evidence",
		dots: 2,
		tone: "link",
		meaning:
			"Some good studies, with gaps or some disagreement. New studies could change this",
	},
	weak: {
		word: "Weak evidence",
		dots: 1,
		tone: "risk.fg",
		meaning: "Few or weaker studies. Treat as a lead, not a finding",
	},
	insufficient: {
		word: "Not enough evidence",
		dots: 0,
		tone: "fg.muted",
		meaning: "The sources don't say enough to judge this option",
	},
};

export const STRENGTH_ORDER: readonly Strength[] = [
	"strong",
	"moderate",
	"weak",
	"insufficient",
];

/** §8.3: why sources disagree, in plain words, with what it means for the decision. */
export const CONFLICTS: Readonly<
	Record<Schemas["ConflictType"], { words: string; help: string }>
> = {
	hierarchy: {
		words: "Stronger and weaker studies disagree",
		help: "Trials or large studies point one way, smaller or weaker designs the other. Give more weight to the stronger ones.",
	},
	context: {
		words: "Different settings, different results",
		help: "The studies looked at different places, people or times. Ask which setting is closest to yours.",
	},
	noise: {
		words: "Small or unclear studies",
		help: "The disagreement comes from small, old or unclear studies. It may not be real.",
	},
};

export function stoppedBecause(reason: StopReason, maxRounds: number): string {
	switch (reason) {
		case "accepted":
			return "accepted by the Critic";
		case "max_rounds":
			return `reached the round limit (${maxRounds})`;
		case "invalid_output":
			return "a step gave unusable answers twice in a row";
		case "no_progress":
			return "no new evidence and no better score";
	}
}

/** The short form for the chat card's heading. */
export function stoppedShort(reason: StopReason): string {
	switch (reason) {
		case "accepted":
			return "accepted";
		case "max_rounds":
			return "round limit reached";
		case "invalid_output":
			return "stopped early";
		case "no_progress":
			return "no further progress";
	}
}

export const ROLES: Readonly<Record<Role, string>> = {
	planner: "Planner",
	retriever: "Retriever",
	writer: "Writer",
	checks: "Checks",
	critic: "Critic",
};

export const STEP_STATUSES: Readonly<Record<StepStatus, string>> = {
	ok: "done",
	refused: "refused",
	invalid: "unusable answer",
	error: "failed",
};

/** The screening label the API recorded for a source (docs/api.md, `DecisionOut.label`). */
export function screeningWord(label: string | null): string | null {
	switch (label) {
		case "include":
			return "Included";
		case "exclude":
			return "Excluded";
		case "uncertain":
			return "Uncertain";
		default:
			return label;
	}
}

/** The Critic's scores, 0 to 2 (§6.9 "Quality checks"), never summed. */
export const SCORES: readonly [keyof Schemas["QualityOut"], string][] = [
	["coverage", "Coverage"],
	["groundedness", "Groundedness"],
	["disagreements", "Disagreements"],
	["uncertainty", "Uncertainty"],
	["humanitarian_awareness", "Humanitarian awareness"],
	["specificity", "Specificity"],
];

export const scoreWord = (value: number): string =>
	value >= 2 ? "good" : value === 1 ? "partly" : "poor";

/** Where a claim sits, for "cited for" (§6.10). */
export const SECTIONS: Readonly<Record<string, string>> = {
	bottom_line: "bottom line",
	benefit: "benefit",
	risk: "risk",
	trade_off: "trade-off",
	conflict: "disagreement",
	consideration: "if this matters most",
};
