/**
 * Words the review screens share (UI_UX_SPEC §6.3, §6.8, §14): mode names,
 * screening labels, sources, durations and times. One place, so a word means
 * the same thing on every screen.
 */

import type { components } from "../../api/schema.gen";

type Schemas = components["schemas"];
export type PlanMode = Schemas["PlanMode"];
export type Tier = Schemas["ScreeningTier"];
/** A row's label: its tier once screened, else why it has none (§6.8). */
export type ShownLabel = Tier | "skipped" | "failed" | "not_screened";

/** The UI says Quick and Deep; the API keeps shallow and deep (§6.3). */
export const MODES: readonly {
	mode: PlanMode;
	name: string;
	description: string;
}[] = [
	{
		mode: "shallow",
		name: "Quick",
		description:
			"About 90 papers, abstracts only, no questions first. Usually 10–20 minutes.",
	},
	{
		mode: "deep",
		name: "Deep",
		description:
			"About 4 questions first, hundreds of papers, full texts where open. Up to an hour or more.",
	},
];

export const modeLabel = (mode: PlanMode): string =>
	mode === "deep" ? "Deep search" : "Quick search";

/** Each label's word, its icon's colour (`tone`) and its tag's fill (`fill`): never colour alone (§9.2.3). */
export const TIERS: Readonly<
	Record<ShownLabel, { word: string; tone: string; fill: string }>
> = {
	highly_relevant: {
		word: "Highly relevant",
		tone: "benefit.fg",
		fill: "benefit.bg",
	},
	relevant: { word: "Relevant", tone: "benefit.fg", fill: "benefit.bg" },
	possibly_relevant: {
		word: "Possibly relevant",
		tone: "masked.fg",
		fill: "masked.bg",
	},
	uncertain: { word: "Uncertain", tone: "caveat.solid", fill: "caveat.subtle" },
	excluded: { word: "Excluded", tone: "fg.muted", fill: "bg.muted" },
	skipped: { word: "Skipped", tone: "fg.muted", fill: "transparent" },
	failed: { word: "Failed", tone: "caveat.solid", fill: "caveat.subtle" },
	not_screened: { word: "Not screened", tone: "fg.muted", fill: "transparent" },
};

/** Every label once, so their counts add up to the run's papers. */
export const TIER_ORDER: readonly ShownLabel[] = [
	"highly_relevant",
	"relevant",
	"possibly_relevant",
	"uncertain",
	"excluded",
	"skipped",
	"failed",
	"not_screened",
];

/** The pipeline's own labels on a screened item ("HIGH · title — reason"). */
const ITEM_LABELS: Readonly<Record<string, Tier>> = {
	HIGH: "highly_relevant",
	MEDIUM: "relevant",
	LOW: "possibly_relevant",
	EXCLUDE: "excluded",
	UNCERTAIN: "uncertain",
};

export interface ScreenedItem {
	tier: Tier | null;
	label: string;
	title: string;
	reason: string | null;
}

/** Read "HIGH · <masked title> — <one-line reason>" (docs/api.md, stage summaries). */
export function readScreenedItem(item: string): ScreenedItem {
	const separator = item.indexOf(" · ");
	const code = separator < 0 ? "" : item.slice(0, separator);
	const rest = separator < 0 ? item : item.slice(separator + 3);
	const dash = rest.indexOf(" — ");
	const tier = ITEM_LABELS[code] ?? null;
	return {
		tier,
		label: tier === null ? code : TIERS[tier].word,
		title: dash < 0 ? rest : rest.slice(0, dash),
		reason: dash < 0 ? null : rest.slice(dash + 3),
	};
}

const SOURCES: Readonly<Record<string, string>> = {
	openalex: "OpenAlex",
	europepmc: "Europe PMC",
	arxiv: "arXiv",
};

export const sourceName = (sourceId: string): string =>
	SOURCES[sourceId] ?? sourceId;

/** "2 s", "4 min", "1 h 5 min": numbers as digits, units short (§14.1). */
export function duration(seconds: number): string {
	if (seconds < 60) {
		return `${Math.round(seconds)} s`;
	}
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) {
		return `${minutes} min`;
	}
	const rest = minutes % 60;
	return `${Math.floor(minutes / 60)} h${rest === 0 ? "" : ` ${rest} min`}`;
}

const CLOCK = new Intl.DateTimeFormat("en-GB", {
	hour: "2-digit",
	minute: "2-digit",
});

/** "10:51". */
export const clockTime = (iso: string): string => CLOCK.format(new Date(iso));

/** "just now", "5 min ago", "3 h ago", else the date (§5.2). */
export function relativeTime(iso: string, now: number = Date.now()): string {
	const minutes = Math.round((now - new Date(iso).getTime()) / 60_000);
	if (minutes < 1) {
		return "just now";
	}
	if (minutes < 60) {
		return `${minutes} min ago`;
	}
	const hours = Math.round(minutes / 60);
	if (hours < 24) {
		return `${hours} h ago`;
	}
	return new Intl.DateTimeFormat("en-GB", {
		day: "numeric",
		month: "short",
	}).format(new Date(iso));
}

export const plural = (count: number, one: string, many = `${one}s`) =>
	`${count} ${count === 1 ? one : many}`;

/** The label a paper's row shows: its tier, else why it has none. */
export const shownLabel = (paper: Schemas["PaperOut"]): ShownLabel =>
	paper.decision?.tier ??
	(paper.screening_status === "screened"
		? "not_screened"
		: paper.screening_status);
