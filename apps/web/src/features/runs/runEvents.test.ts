import { describe, expect, it } from "vitest";
import {
	applyRunEvent,
	initialRunState,
	parseRunEvent,
	type RunEvent,
	type RunState,
} from "./runEvents";

function event(type: string, id: number, data: unknown): RunEvent {
	const parsed = parseRunEvent(type, String(id), JSON.stringify(data));
	if (parsed === null) {
		throw new Error(`fixture ${type} did not parse`);
	}
	return parsed;
}

function applyAll(
	events: RunEvent[],
	state: RunState = initialRunState,
): RunState {
	return events.reduce(applyRunEvent, state);
}

const started = event("stage.started", 1, {
	stage: "screen",
	position: 5,
	total: 8,
	at: "2026-11-03T10:51:00Z",
});
const progress = event("stage.progress", 2, {
	stage: "screen",
	done: 34,
	total: 90,
	item: "HIGH · Cash transfers and food prices in Mali",
});
const step = event("agent.step", 3, { round: 1, role: "writer", status: "ok" });

describe("reading an event off the stream", () => {
	it("keeps the id the server gave it", () => {
		expect(started.id).toBe(1);
	});

	it.each([
		["an unknown event name", "stage.exploded", "4", "{}"],
		[
			"an id that is not a number",
			"stage.started",
			"four",
			'{"stage":"screen","position":1,"total":1}',
		],
		["data that is not JSON", "stage.started", "4", "not json"],
		["data missing its fields", "stage.progress", "4", '{"stage":"screen"}'],
	])("refuses %s", (_, type, id, data) => {
		expect(parseRunEvent(type, id, data)).toBeNull();
	});
});

describe("applying events (UI_UX_SPEC §11.2)", () => {
	it("follows a stage from start to progress", () => {
		const state = applyAll([started, progress]);

		expect(state.status).toBe("running");
		expect(state.currentStage).toBe("screen");
		expect(state.stages).toEqual([
			{
				stage: "screen",
				status: "running",
				done: 34,
				total: 90,
				seconds: null,
			},
		]);
		expect(state.latestItem).toBe(
			"HIGH · Cash transfers and food prices in Mali",
		);
	});

	it("ignores an event it has already applied, as a replay after reconnecting sends", () => {
		const once = applyAll([started, progress, step]);

		const replayed = applyAll([started, progress, step], once);

		expect(replayed).toBe(once);
		expect(replayed.agentSteps).toHaveLength(1);
	});

	it("pauses on a gate and resumes when it is resolved", () => {
		const waiting = applyAll([
			started,
			event("gate.required", 2, {
				gate_id: "g-1",
				kind: "privacy_review",
				payload: {},
			}),
		]);
		expect(waiting.status).toBe("waiting");
		expect(waiting.gate).toEqual({ gateId: "g-1", kind: "privacy_review" });

		const resumed = applyRunEvent(
			waiting,
			event("gate.resolved", 3, {
				gate_id: "g-1",
				by: "u-1",
				decision: "approve",
			}),
		);
		expect(resumed.status).toBe("running");
		expect(resumed.gate).toBeNull();
	});

	it("marks a finished stage done with its duration", () => {
		const state = applyAll([
			started,
			progress,
			event("stage.finished", 3, {
				stage: "screen",
				seconds: 540,
				summary: {},
			}),
		]);

		expect(state.stages[0]).toMatchObject({ status: "done", seconds: 540 });
	});

	it.each([
		["done", null, "done"],
		["failed", "The model server isn't answering.", "failed"],
		["cancelled", null, "cancelled"],
	] as const)("ends a run that finished as %s", (outcome, error, status) => {
		const state = applyAll([
			started,
			event("job.finished", 2, { outcome, seconds: 1620, error }),
		]);

		expect(state.status).toBe(status);
		expect(state.isFinished).toBe(true);
		expect(state.error).toBe(error);
	});
});

describe("what the run screens read from the events (UI_UX_SPEC §5.3.5, §6.7)", () => {
	it("refuses a stage the API does not have", () => {
		expect(
			parseRunEvent(
				"stage.started",
				"1",
				'{"stage":"teleport","position":1,"total":1}',
			),
		).toBeNull();
	});

	it("remembers when the run's first stage started", () => {
		expect(applyAll([started]).startedAt).toBe("2026-11-03T10:51:00Z");
	});

	it("keeps each finished stage's summary for the cards that report it", () => {
		const state = applyAll([
			started,
			event("stage.finished", 2, {
				stage: "screen",
				seconds: 540,
				summary: { papers: 90, screened: 88 },
			}),
		]);

		expect(state.summaries["screen"]).toEqual({ papers: 90, screened: 88 });
	});

	it("lists the items a stage reported, newest first, holding the last 200", () => {
		const items = Array.from({ length: 205 }, (_, n) =>
			event("stage.progress", n + 2, {
				stage: "screen",
				done: n + 1,
				total: 205,
				item: `HIGH · Paper ${n + 1} — reason`,
			}),
		);

		const state = applyAll([started, ...items]);

		expect(state.items).toHaveLength(200);
		expect(state.items[0]).toEqual({
			id: 206,
			stage: "screen",
			item: "HIGH · Paper 205 — reason",
		});
	});

	it("keeps what a gate asks the person to decide", () => {
		const state = applyAll([
			started,
			event("gate.required", 2, {
				gate_id: "g-1",
				kind: "vocabulary",
				payload: { rendered: '("cash")' },
			}),
		]);

		expect(state.gatePayload).toEqual({ rendered: '("cash")' });
	});

	it("marks the stage a run stopped at", () => {
		const state = applyAll([
			started,
			event("job.finished", 2, {
				outcome: "failed",
				seconds: 60,
				error: "The model server isn't answering.",
			}),
		]);

		expect(state.stages[0]?.status).toBe("failed");
	});
});
