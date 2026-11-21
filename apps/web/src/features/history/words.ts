/**
 * The words of Needs you, Recent activity and the timeline: L0 lines built
 * from the facts the API sends (UI_UX_SPEC §5.1, §14), and the day groups.
 * Which items exist is the API's decision; this only says them.
 */

import type { components } from "../../api/schema.gen";
import { deletedInText } from "../retention/words";
import { duration, plural } from "../reviews/format";
import { isStageId } from "../runs/runEvents";
import { GATES, stageName } from "../runs/stages";

type Schemas = components["schemas"];
export type ActivityEvent = Schemas["ActivityEventOut"];
export type NeedsYouItem = Schemas["NeedsYouItemOut"];
export type ActivityKind = Schemas["ActivityKind"];

const stageWord = (stage: string | null): string =>
	stage !== null && isStageId(stage) ? stageName(stage) : "an unknown stage";

const quoted = (text: string | null): string => `"${text ?? ""}"`;

/** " · qwen3.5:9b": the model is named on every run and brief line (§7.4). */
const models = (event: ActivityEvent): string =>
	event.model_ids.length === 0 ? "" : ` · ${event.model_ids.join(", ")}`;

function runText(event: ActivityEvent): string {
	const run = `Run ${event.run_number ?? ""}`;
	const took = event.seconds === null ? "" : ` · ${duration(event.seconds)}`;
	switch (event.run_state) {
		case "done":
			return `${run} finished${took}${models(event)}`;
		case "failed": {
			const why = event.error === null ? "" : ` (${event.error})`;
			return `${run} stopped at ${stageWord(event.stopped_at)}${why}${models(event)}`;
		}
		case "cancelled":
			return `${run} cancelled at ${stageWord(event.stopped_at)}`;
		case "waiting":
			return `${run} waiting for a decision${models(event)}`;
		default:
			return `${run} running${models(event)}`;
	}
}

/** A key for React lists: what the line is about, and when. */
export const eventKey = (event: ActivityEvent): string =>
	[
		event.action,
		event.project.id,
		event.review_id,
		event.run_id ?? event.brief_id ?? event.thread_id ?? event.document_id,
		event.version,
		event.at,
	].join(":");

/** One activity line, without its time or project. */
export function eventText(event: ActivityEvent): string {
	const who = event.actor?.display_name ?? "Someone";
	switch (event.action) {
		case "review_started":
			return `${who} asked ${quoted(event.text)}`;
		case "plan_drafted":
			return `Search plan v${event.version ?? ""} drafted`;
		case "plan_accepted":
			return `Search plan v${event.version ?? ""} accepted by ${who}`;
		case "labels_changed":
			return `Labels changed by new rules · ${plural(event.papers_moved ?? 0, "paper")} moved`;
		case "run":
			return runText(event);
		case "brief_ready":
			return `Brief v${event.version ?? ""} ready${models(event)}`;
		case "question_asked":
			return `${who} asked ${quoted(event.text)}${models(event)}`;
		case "document_added":
			return `Document D${event.document_number ?? ""} added: ${event.text ?? ""}`;
	}
}

/** What one Needs you row says, without its project. */
export function needsYouText(item: NeedsYouItem): string {
	switch (item.kind) {
		case "gate":
			return `${item.gate_kind === null ? "Decision" : GATES[item.gate_kind]} · run ${item.run_number ?? ""} is waiting`;
		case "draft":
			return `Draft ${item.version ?? ""} waiting for your accept`;
		case "failed_run":
			return `Run ${item.run_number ?? ""} stopped at ${stageWord(item.stopped_at)}`;
		case "unread_brief":
			return `Brief v${item.version ?? ""} ready to read`;
		case "retention":
			return deletedInText(item.days_left ?? 0);
	}
}

/** The verb of a row's one action, as on its button. */
export function needsYouAction(item: NeedsYouItem): string {
	switch (item.kind) {
		case "gate":
			return "Review";
		case "unread_brief":
			return "Read";
		case "retention":
			return "Keep";
		default:
			return "Open";
	}
}

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "long" });
const MONTH_YEAR = new Intl.DateTimeFormat("en-GB", {
	month: "long",
	year: "numeric",
});

function startOfDay(date: Date): number {
	return new Date(
		date.getFullYear(),
		date.getMonth(),
		date.getDate(),
	).getTime();
}

const DAY = 86_400_000;

/** "Today", "Yesterday", "This week", then month names (§7.1), in local time. */
export function dayGroup(iso: string, now: Date = new Date()): string {
	const at = new Date(iso);
	const days = Math.round((startOfDay(now) - startOfDay(at)) / DAY);
	if (days <= 0) {
		return "Today";
	}
	if (days === 1) {
		return "Yesterday";
	}
	if (days < 7) {
		return "This week";
	}
	return at.getFullYear() === now.getFullYear()
		? MONTH.format(at)
		: MONTH_YEAR.format(at);
}

/** Consecutive items under their day group, keeping their order. */
export function byDay<T extends { at: string }>(
	items: readonly T[],
	now: Date = new Date(),
): { group: string; items: T[] }[] {
	const groups: { group: string; items: T[] }[] = [];
	for (const item of items) {
		const group = dayGroup(item.at, now);
		const last = groups.at(-1);
		if (last?.group === group) {
			last.items.push(item);
		} else {
			groups.push({ group, items: [item] });
		}
	}
	return groups;
}

export const ACTIVITY_KINDS: readonly {
	kind: ActivityKind;
	name: string;
}[] = [
	{ kind: "review", name: "Reviews" },
	{ kind: "run", name: "Runs" },
	{ kind: "brief", name: "Briefs" },
	{ kind: "question", name: "Questions" },
	{ kind: "document", name: "Documents" },
];
