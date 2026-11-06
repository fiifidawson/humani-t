/**
 * A run's live events, read off the SSE stream and folded into one state.
 *
 * The payload types are the API's own (`RunStreamEvent` in the generated
 * schema), so a contract change breaks the type check. Every field is still
 * checked at runtime, because the stream is input like any other.
 *
 * Pure: no I/O, no timers. `useRunEvents` owns the connection.
 */

import type { components } from "../../api/schema.gen";

type Schemas = components["schemas"];

export type StageId = Schemas["StageStartedData"]["stage"];
type GateKind = Schemas["GateRequiredData"]["kind"];
type Outcome = Schemas["JobFinishedData"]["outcome"];

/** Pipeline order. A `Record` so a stage added to the API fails the type check here. */
const STAGE_ORDER: Readonly<Record<StageId, number>> = {
	queries: 1,
	discover: 2,
	ingest: 3,
	mask: 4,
	screen: 5,
	export: 6,
	index: 7,
	answer: 8,
};

export const PIPELINE: readonly StageId[] = (
	Object.keys(STAGE_ORDER) as StageId[]
).sort((a, b) => STAGE_ORDER[a] - STAGE_ORDER[b]);

export const isStageId = (value: unknown): value is StageId =>
	typeof value === "string" && Object.hasOwn(STAGE_ORDER, value);

export const RUN_EVENT_NAMES = [
	"stage.started",
	"stage.progress",
	"stage.finished",
	"agent.step",
	"gate.required",
	"gate.resolved",
	"job.finished",
] as const satisfies readonly Schemas["RunStreamEvent"]["event"][];

export type RunEventName = (typeof RUN_EVENT_NAMES)[number];

export type AgentStep = Pick<
	Schemas["AgentStepData"],
	"round" | "role" | "status"
>;
export type Summary = Schemas["StageFinishedData"]["summary"];

type Payloads = {
	"stage.started": Pick<
		Schemas["StageStartedData"],
		"stage" | "position" | "total"
	> & { at: string | null };
	"stage.progress": Schemas["StageProgressData"];
	"stage.finished": Schemas["StageFinishedData"];
	"agent.step": AgentStep;
	"gate.required": { gateId: string; kind: GateKind; payload: Summary };
	"gate.resolved": { gateId: string };
	"job.finished": Schemas["JobFinishedData"];
};

export type RunEvent = {
	[Name in RunEventName]: { type: Name; id: number; data: Payloads[Name] };
}[RunEventName];

export interface StageProgress {
	stage: StageId;
	status: "running" | "done" | "failed" | "cancelled";
	done: number | null;
	total: number | null;
	seconds: number | null;
}

export interface ReportedItem {
	id: number;
	stage: StageId;
	item: string;
}

export type RunStatus =
	| "idle"
	| "running"
	| "waiting"
	| "done"
	| "failed"
	| "cancelled";

export interface RunState {
	/** The id of the last event applied; anything at or below it is a replay. */
	lastEventId: number;
	status: RunStatus;
	currentStage: StageId | null;
	stages: StageProgress[];
	latestItem: string | null;
	/** What stages reported item by item, newest first, the last MAX_ITEMS. */
	items: ReportedItem[];
	summaries: Partial<Record<StageId, Summary>>;
	gate: { gateId: string; kind: GateKind } | null;
	gatePayload: Summary | null;
	agentSteps: AgentStep[];
	startedAt: string | null;
	error: string | null;
	seconds: number | null;
	isFinished: boolean;
}

/** The live list holds this many rows; "Open papers" shows the rest (UI_UX_SPEC §6.7). */
export const MAX_ITEMS = 200;

export const initialRunState: RunState = {
	lastEventId: 0,
	status: "idle",
	currentStage: null,
	stages: [],
	latestItem: null,
	items: [],
	summaries: {},
	gate: null,
	gatePayload: null,
	agentSteps: [],
	startedAt: null,
	error: null,
	seconds: null,
	isFinished: false,
};

type Fields = Record<string, unknown>;

const isRecord = (value: unknown): value is Fields =>
	typeof value === "object" && value !== null && !Array.isArray(value);
const str = (fields: Fields, key: string) =>
	typeof fields[key] === "string" ? (fields[key] as string) : undefined;
const num = (fields: Fields, key: string) =>
	typeof fields[key] === "number" && Number.isFinite(fields[key])
		? (fields[key] as number)
		: undefined;

const OUTCOMES = ["done", "failed", "cancelled"] as const satisfies Outcome[];
const GATE_KINDS = [
	"vocabulary",
	"privacy_review",
] as const satisfies GateKind[];
const ROLES = [
	"planner",
	"retriever",
	"writer",
	"checks",
	"critic",
] as const satisfies AgentStep["role"][];
const STEP_STATUSES = [
	"ok",
	"refused",
	"invalid",
	"error",
] as const satisfies AgentStep["status"][];

const oneOf = <T extends string>(options: readonly T[], value: unknown) =>
	options.find((option) => option === value);

function readPayload(
	type: RunEventName,
	fields: Fields,
): RunEvent["data"] | null {
	const stage = isStageId(fields["stage"]) ? fields["stage"] : undefined;
	switch (type) {
		case "stage.started": {
			const position = num(fields, "position");
			const total = num(fields, "total");
			return stage && position !== undefined && total !== undefined
				? { stage, position, total, at: str(fields, "at") ?? null }
				: null;
		}
		case "stage.progress": {
			const done = num(fields, "done");
			const total = num(fields, "total");
			return stage && done !== undefined && total !== undefined
				? { stage, done, total, item: str(fields, "item") ?? null }
				: null;
		}
		case "stage.finished": {
			const seconds = num(fields, "seconds");
			const summary = isRecord(fields["summary"]) ? fields["summary"] : {};
			return stage && seconds !== undefined
				? { stage, seconds, summary }
				: null;
		}
		case "agent.step": {
			const round = num(fields, "round");
			const role = oneOf(ROLES, fields["role"]);
			const status = oneOf(STEP_STATUSES, fields["status"]);
			return round !== undefined && role && status
				? { round, role, status }
				: null;
		}
		case "gate.required": {
			const gateId = str(fields, "gate_id");
			const kind = oneOf(GATE_KINDS, fields["kind"]);
			const payload = isRecord(fields["payload"]) ? fields["payload"] : {};
			return gateId && kind ? { gateId, kind, payload } : null;
		}
		case "gate.resolved": {
			const gateId = str(fields, "gate_id");
			return gateId ? { gateId } : null;
		}
		case "job.finished": {
			const outcome = oneOf(OUTCOMES, fields["outcome"]);
			const seconds = num(fields, "seconds");
			const error = fields["error"];
			return outcome &&
				seconds !== undefined &&
				(error === null || typeof error === "string")
				? { outcome, seconds, error: error ?? null }
				: null;
		}
	}
}

/** One SSE message as a typed event, or `null` if anything about it is wrong. */
export function parseRunEvent(
	type: string,
	lastEventId: string,
	data: string,
): RunEvent | null {
	const name = RUN_EVENT_NAMES.find((candidate) => candidate === type);
	if (name === undefined || !/^\d+$/.test(lastEventId)) {
		return null;
	}
	let fields: unknown;
	try {
		fields = JSON.parse(data);
	} catch {
		return null;
	}
	const payload = isRecord(fields) ? readPayload(name, fields) : null;
	return payload === null
		? null
		: ({ type: name, id: Number(lastEventId), data: payload } as RunEvent);
}

function withStage(
	stages: StageProgress[],
	stage: StageId,
	change: (current: StageProgress) => StageProgress,
): StageProgress[] {
	const fresh: StageProgress = {
		stage,
		status: "running",
		done: null,
		total: null,
		seconds: null,
	};
	const exists = stages.some((s) => s.stage === stage);
	const all = exists ? stages : [...stages, fresh];
	return all.map((s) => (s.stage === stage ? change(s) : s));
}

function stoppedAt(state: RunState, outcome: Outcome): StageProgress[] {
	if (outcome === "done" || state.currentStage === null) {
		return state.stages;
	}
	return state.stages.map((s) =>
		s.stage === state.currentStage && s.status === "running"
			? { ...s, status: outcome }
			: s,
	);
}

/**
 * Apply one event. Returns the same object when the event was already applied.
 *
 * Ids increase by one within a run (architecture §4.4), so "already seen" is
 * "not above the last id". A replay after a reconnect, or after an id the
 * server did not recognise, can repeat events; this is where they stop.
 */
export function applyRunEvent(state: RunState, event: RunEvent): RunState {
	if (event.id <= state.lastEventId || state.isFinished) {
		return state;
	}
	const next: RunState = { ...state, lastEventId: event.id };
	switch (event.type) {
		case "stage.started":
			return {
				...next,
				status: state.gate === null ? "running" : "waiting",
				currentStage: event.data.stage,
				startedAt: state.startedAt ?? event.data.at,
				stages: withStage(state.stages, event.data.stage, (s) => ({
					...s,
					status: "running",
				})),
			};
		case "stage.progress": {
			const { item } = event.data;
			return {
				...next,
				currentStage: event.data.stage,
				latestItem: item ?? state.latestItem,
				items:
					item === null
						? state.items
						: [
								{ id: event.id, stage: event.data.stage, item },
								...state.items,
							].slice(0, MAX_ITEMS),
				stages: withStage(state.stages, event.data.stage, (s) => ({
					...s,
					done: event.data.done,
					total: event.data.total,
				})),
			};
		}
		case "stage.finished":
			return {
				...next,
				summaries: {
					...state.summaries,
					[event.data.stage]: event.data.summary,
				},
				stages: withStage(state.stages, event.data.stage, (s) => ({
					...s,
					status: "done",
					seconds: event.data.seconds,
				})),
			};
		case "agent.step":
			return { ...next, agentSteps: [...state.agentSteps, event.data] };
		case "gate.required":
			return {
				...next,
				status: "waiting",
				gate: { gateId: event.data.gateId, kind: event.data.kind },
				gatePayload: event.data.payload,
			};
		case "gate.resolved":
			return state.gate?.gateId === event.data.gateId
				? { ...next, status: "running", gate: null, gatePayload: null }
				: next;
		case "job.finished":
			return {
				...next,
				status: event.data.outcome,
				stages: stoppedAt(state, event.data.outcome),
				seconds: event.data.seconds,
				error: event.data.error,
				gate: null,
				gatePayload: null,
				isFinished: true,
			};
	}
}
