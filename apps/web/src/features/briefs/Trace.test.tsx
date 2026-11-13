import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function briefed(hasBrief = true, state: "running" | "done" = "done") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "owner");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, state);
	if (hasBrief) {
		api.reviews.addBrief(review, run);
	}
	return { project, review, run, path: `/p/${project.id}/r/${review.id}` };
}

async function openTrace(path: string) {
	const view = renderApp(`${path}/trace`);
	await screen.findByText(/Stopped because/);
	const canvas = screen.getByRole("region", {
		name: "How this brief was made",
	});
	return { ...view, canvas };
}

describe("how the brief was made (UI_UX_SPEC §6.11)", () => {
	it("opens from the brief's header", async () => {
		const { path, project, review } = briefed();
		const { user, router } = renderApp(`${path}/brief`);
		await screen.findByRole("navigation", { name: "Sections of this brief" });
		const brief = screen.getByRole("region", { name: "Decision brief" });

		await user.click(
			await within(brief).findByRole("link", { name: "How this was made" }),
		);

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}/trace`),
		);
		// While the trace loads, a placeholder region with the same name stands in,
		// and the loaded one replaces it: wait for the trace itself, then look up.
		await screen.findByText(/Stopped because/);
		expect(
			screen.getByRole("region", { name: "How this brief was made" }),
		).toHaveTextContent("Stopped because");
	});

	it("says why the loop stopped and which round was kept", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(canvas).toHaveTextContent(
			"Stopped because: accepted by the Critic · kept round 2 of 3",
		);
	});

	it("tells the loop as a timeline, round by round, one row per step", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const before = within(canvas).getByRole("list", {
			name: "Before the first draft",
		});
		expect(
			within(before)
				.getAllByRole("listitem")
				.map((row) => row.textContent),
		).toEqual([
			expect.stringContaining("Planner · 1 call · 41 s"),
			expect.stringContaining(
				"Retriever · 2 searches · 5 new passages (5 in all)",
			),
		]);
		const first = within(canvas).getByRole("list", { name: "Round 1" });
		const rows = within(first).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent("Writer · 3 options · 14 claims · 2 min");
		expect(rows[1]).toHaveTextContent("Checks · 1 problem");
		expect(rows[2]).toHaveTextContent("Critic · revise");
	});

	it("shows a failed step with its short error", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const second = within(canvas).getByRole("list", { name: "Round 2" });
		expect(within(second).getAllByRole("listitem")[0]).toHaveTextContent(
			"Writer · failed · The model server isn't answering.",
		);
	});

	it("opens a step to its detail: the plan, the searches, the checks' problems, the Critic's requests", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);

		await user.click(
			within(canvas).getByRole("button", { name: "Planner · 1 call · 41 s" }),
		);
		expect(canvas).toHaveTextContent(
			"Decision: Choose between cash, vouchers or in-kind food",
		);
		expect(canvas).toHaveTextContent(
			"Options: Cash transfers · Vouchers · In-kind food",
		);

		await user.click(
			within(canvas).getByRole("button", { name: "Checks · 1 problem" }),
		);
		expect(canvas).toHaveTextContent("In-kind food has no sourced benefit.");

		await user.click(
			within(canvas).getByRole("button", { name: "Critic · revise" }),
		);
		expect(canvas).toHaveTextContent(
			"Asked for: price effects of cash in small markets",
		);
	});

	it("ends with the totals and what the run used", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(canvas).toHaveTextContent(
			"Model: qwen3.5:9b · 9 calls · 61,200 tokens in, 8,900 out",
		);
		const used = within(canvas).getByRole("list", { name: "What this used" });
		expect(used).toHaveTextContent("Retriever: bm25");
		expect(used).toHaveTextContent(
			"Prompt versions: answer.writer brief.writer@2",
		);
	});

	it("says when masking was off for the run, as the owner chose", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		const project = api.addProject("Cash vs vouchers, Gao", "owner");
		const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
		api.reviews.startPlan(review, "shallow");
		api.reviews.accept(review);
		api.reviews.addBrief(review, api.reviews.addRun(review, "done"));
		const entry = api.reviews.briefs[0];
		if (entry !== undefined) {
			entry.trace.is_masked = false;
		}
		const { canvas } = await openTrace(`/p/${project.id}/r/${review.id}`);

		const used = within(canvas).getByRole("list", { name: "What this used" });
		expect(used).toHaveTextContent(
			"Masking: off for this run (the owner's choice)",
		);
	});

	it("says masking was on when it was", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const used = within(canvas).getByRole("list", { name: "What this used" });
		expect(used).toHaveTextContent("Masking: on");
	});

	it("shows the steps live while the brief is still being written", async () => {
		const { path, run } = briefed(false, "running");
		renderApp(`${path}/trace`);
		const source = await waitFor(() => {
			const found = FakeEventSource.instances.find((s) =>
				s.url.includes(run.id),
			);
			expect(found).toBeDefined();
			return found as FakeEventSource;
		});
		act(() => {
			source.open();
			source.emit("stage.started", 1, {
				stage: "answer",
				position: 8,
				total: 8,
				at: "2026-11-03T10:51:00Z",
			});
			source.emit("agent.step", 2, { round: 0, role: "planner", status: "ok" });
			source.emit("agent.step", 3, { round: 1, role: "writer", status: "ok" });
		});

		await screen.findByText("Still writing. Steps appear as they finish.");
		const canvas = screen.getByRole("region", {
			name: "How this brief was made",
		});
		expect(canvas).toHaveTextContent(
			"Still writing. Steps appear as they finish.",
		);
		const live = within(canvas).getByRole("list", { name: "Steps so far" });
		expect(
			within(live)
				.getAllByRole("listitem")
				.map((li) => li.textContent),
		).toEqual(["Planner · round 0 · done", "Writer · round 1 · done"]);
	});

	it("has no accessibility violations", async () => {
		const { path } = briefed();
		const { container } = await openTrace(path);

		await expectNoAccessibilityViolations(container);
	});
});
