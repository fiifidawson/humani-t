import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderWithBrand } from "../../test/render";
import { RunStrip } from "./RunStrip";
import {
	applyRunEvent,
	initialRunState,
	parseRunEvent,
	type RunEvent,
	type RunState,
} from "./runEvents";

function parsed(type: string, id: number, data: unknown): RunEvent {
	const event = parseRunEvent(type, String(id), JSON.stringify(data));
	if (event === null) {
		throw new Error(`fixture ${type} did not parse`);
	}
	return event;
}

function stateAfter(...events: [string, number, unknown][]): RunState {
	return events.reduce(
		(state, [type, id, data]) => applyRunEvent(state, parsed(type, id, data)),
		initialRunState,
	);
}

const screening = stateAfter(
	[
		"stage.started",
		1,
		{ stage: "screen", position: 5, total: 8, at: "2026-11-03T10:51:00Z" },
	],
	["stage.progress", 2, { stage: "screen", done: 34, total: 90 }],
);

describe("the run strip, one line (UI_UX_SPEC §5.3.5)", () => {
	it("says which stage is running and how far it has got, in words", () => {
		renderWithBrand(<RunStrip state={screening} connection="live" />);

		expect(screen.getByText("Screening · 34 of 90")).toBeInTheDocument();
		expect(screen.getByText("Running")).toBeInTheDocument();
	});

	it("says when a run waits for a person", () => {
		const waiting = stateAfter(
			[
				"stage.started",
				1,
				{ stage: "mask", position: 4, total: 8, at: "2026-11-03T10:51:00Z" },
			],
			[
				"gate.required",
				2,
				{ gate_id: "g-1", kind: "privacy_review", payload: {} },
			],
		);

		renderWithBrand(<RunStrip state={waiting} connection="live" />);

		expect(
			screen.getByText("Waiting for you · Privacy review"),
		).toBeInTheDocument();
	});

	it("says where a run stopped and why, in the API's words", () => {
		const failed = stateAfter(
			[
				"stage.started",
				1,
				{ stage: "screen", position: 5, total: 8, at: "2026-11-03T10:51:00Z" },
			],
			[
				"job.finished",
				2,
				{
					outcome: "failed",
					seconds: 600,
					error: "The model server isn't answering.",
				},
			],
		);

		renderWithBrand(<RunStrip state={failed} connection="closed" />);

		expect(
			screen.getByText(
				"Run stopped at Screening · The model server isn't answering.",
			),
		).toBeInTheDocument();
	});

	it("shows Reconnecting… and keeps the last state while the stream is down", () => {
		renderWithBrand(<RunStrip state={screening} connection="reconnecting" />);

		expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
		expect(screen.getByText("Screening · 34 of 90")).toBeInTheDocument();
	});

	it("announces stage changes, not every count", () => {
		const { rerender } = renderWithBrand(
			<RunStrip state={screening} connection="live" />,
		);
		const announcer = screen.getByRole("status");
		expect(announcer).toHaveTextContent("Screening started.");

		const moreProgress = applyRunEvent(
			screening,
			parsed("stage.progress", 3, { stage: "screen", done: 35, total: 90 }),
		);
		rerender(<RunStrip state={moreProgress} connection="live" />);

		expect(announcer).toHaveTextContent("Screening started.");
		expect(announcer).not.toHaveTextContent("35");
	});

	it("has no accessibility violations", async () => {
		const { container } = renderWithBrand(
			<RunStrip state={screening} connection="live" />,
		);

		await expectNoAccessibilityViolations(container);
	});
});
