import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../../test/fakeEventSource";
import { useRunEvents } from "./useRunEvents";

const RUN_EVENTS_URL = "/api/v1/runs/r-1/events";

function follow() {
	return renderHook(() =>
		useRunEvents(RUN_EVENTS_URL, {
			createEventSource: (url) => new FakeEventSource(url),
		}),
	);
}

function emit(type: string, id: number, data: unknown) {
	act(() => FakeEventSource.latest().emit(type, id, data));
}

const started = (id: number) =>
	[
		"stage.started",
		id,
		{ stage: "screen", position: 5, total: 8, at: "2026-11-03T10:51:00Z" },
	] as const;
const step = (id: number) =>
	["agent.step", id, { round: 1, role: "writer", status: "ok" }] as const;
const progress = (id: number, done: number) =>
	["stage.progress", id, { stage: "screen", done, total: 90 }] as const;

beforeEach(() => {
	FakeEventSource.reset();
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe("following a run's events (architecture §4.4, UI_UX_SPEC §11.2)", () => {
	it("opens one stream for the run", () => {
		follow();

		expect(FakeEventSource.instances.map((source) => source.url)).toEqual([
			RUN_EVENTS_URL,
		]);
	});

	it("says it is live once the stream opens", () => {
		const { result } = follow();
		expect(result.current.connection).toBe("connecting");

		act(() => FakeEventSource.latest().open());

		expect(result.current.connection).toBe("live");
	});

	it("keeps the last state and says it is reconnecting while the browser retries", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));

		act(() => FakeEventSource.latest().drop());

		expect(result.current.connection).toBe("reconnecting");
		expect(result.current.state.currentStage).toBe("screen");
	});

	it("applies each event once when the server replays after a reconnect", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...step(2));

		act(() => FakeEventSource.latest().drop());
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...step(2));
		emit(...step(3));

		expect(result.current.state.agentSteps).toHaveLength(2);
		expect(result.current.state.lastEventId).toBe(3);
	});

	it("opens a new stream when the browser gives up, and still applies nothing twice", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...step(2));

		act(() => FakeEventSource.latest().fail());
		expect(result.current.connection).toBe("reconnecting");
		act(() => {
			vi.advanceTimersByTime(1000);
		});

		expect(FakeEventSource.instances).toHaveLength(2);
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...step(2));
		expect(result.current.state.agentSteps).toHaveLength(1);
	});

	it("asks a new stream to resume after the last event it applied", () => {
		follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...step(2));

		act(() => FakeEventSource.latest().fail());
		act(() => {
			vi.advanceTimersByTime(1000);
		});

		expect(FakeEventSource.latest().url).toBe(
			`${RUN_EVENTS_URL}?last_event_id=2`,
		);
	});

	it("closes the stream after the run finishes, so a finished run never reconnects", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());

		emit("job.finished", 1, { outcome: "done", seconds: 1620, error: null });
		act(() => {
			vi.advanceTimersByTime(60_000);
		});

		expect(FakeEventSource.latest().isClosed).toBe(true);
		expect(FakeEventSource.instances).toHaveLength(1);
		expect(result.current.connection).toBe("closed");
		expect(result.current.state.status).toBe("done");
	});

	it("redraws progress at most twice a second, and always ends on the latest count", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));

		for (let done = 1; done <= 10; done += 1) {
			emit(...progress(done + 1, done));
		}
		expect(result.current.state.stages[0]?.done).toBe(1);

		act(() => {
			vi.advanceTimersByTime(500);
		});
		expect(result.current.state.stages[0]?.done).toBe(10);
	});

	it("shows other events at once, even mid-throttle", () => {
		const { result } = follow();
		act(() => FakeEventSource.latest().open());
		emit(...started(1));
		emit(...progress(2, 1));
		emit(...progress(3, 2));

		emit("gate.required", 4, {
			gate_id: "g-1",
			kind: "privacy_review",
			payload: {},
		});

		expect(result.current.state.status).toBe("waiting");
		expect(result.current.state.stages[0]?.done).toBe(2);
	});

	it("stops listening when nothing shows the run any more", () => {
		const { unmount } = follow();

		unmount();

		expect(FakeEventSource.latest().isClosed).toBe(true);
	});
});
