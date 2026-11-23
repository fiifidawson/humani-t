import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { loopAgent, loopDiagram } from "../../test/agentLoop";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

/** The value "What this used" gives for `name`: the definition after its term. */
function used(canvas: HTMLElement, name: string): HTMLElement {
	const term = within(canvas).getByText(name, { selector: "dt" });
	const value = term.nextElementSibling;
	if (!(value instanceof HTMLElement) || value.tagName !== "DD") {
		throw new Error(`No value after "${name}"`);
	}
	return value;
}

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
	return { api, project, review, run, path: `/p/${project.id}/r/${review.id}` };
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

	it("opens the loop on the latest round, with a picker for the others", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const picker = within(canvas).getByRole("group", { name: "Round" });
		expect(
			within(picker)
				.getAllByRole("button")
				.map((button) => button.textContent),
		).toEqual(["Before the first draft", "Round 1", "Round 2"]);
		expect(
			within(picker).getByRole("button", { name: "Round 2" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			within(canvas).queryByRole("list", { name: "Round 1" }),
		).not.toBeInTheDocument();
		const rows = within(
			within(canvas).getByRole("list", { name: "Round 2" }),
		).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent(
			"Writer · failed · The model server isn't answering.",
		);
		expect(rows[1]).toHaveTextContent("Critic · accept");
	});

	it("switches rounds from the picker, one row per step", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);

		await user.click(
			within(canvas).getByRole("button", { name: "Before the first draft" }),
		);
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

		await user.click(within(canvas).getByRole("button", { name: "Round 1" }));
		expect(
			within(canvas).getByRole("button", { name: "Round 1" }),
		).toHaveAttribute("aria-pressed", "true");
		const rows = within(
			within(canvas).getByRole("list", { name: "Round 1" }),
		).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent("Writer · 3 options · 14 claims · 2 min");
		expect(rows[1]).toHaveTextContent("Checks · 1 problem");
		expect(rows[2]).toHaveTextContent("Critic · revise");
	});

	it("draws each agent with an icon, its state in words and the round's result", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);
		await user.click(within(canvas).getByRole("button", { name: "Round 1" }));

		const planner = loopAgent(canvas, "Planner");
		expect(planner).toHaveTextContent("done");
		expect(planner).toHaveTextContent("1 call · 41 s");
		expect(planner.querySelector("svg")).not.toBeNull();
		const writer = loopAgent(canvas, "Writer");
		expect(writer).toHaveTextContent("done");
		expect(writer).toHaveTextContent("3 options · 14 claims · 2 min");
		expect(loopAgent(canvas, "Critic")).toHaveTextContent("revise");
	});

	it("marks a failed agent with a word and its short error, not colour alone", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const writer = loopAgent(canvas, "Writer");
		expect(writer).toHaveTextContent("failed");
		expect(writer).toHaveTextContent("The model server isn't answering.");
	});

	it("mutes an agent that did not run in the round", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);
		await user.click(
			within(canvas).getByRole("button", { name: "Before the first draft" }),
		);

		expect(loopAgent(canvas, "Retriever")).toHaveTextContent(
			"2 searches · 5 new passages (5 in all)",
		);
		expect(loopAgent(canvas, "Writer")).toHaveTextContent("not run");
		expect(loopAgent(canvas, "Critic")).toHaveTextContent("not run");
	});

	it("shows the checks as a badge on the way from the Writer to the Critic", async () => {
		const { api, path } = briefed();
		api.reviews.briefs[0]?.trace.steps.push({
			round: 2,
			role: "checks",
			status: "ok",
			seconds: 0,
			problems: [],
		});
		const { canvas, user } = await openTrace(path);

		expect(loopAgent(canvas, "Checks")).toHaveTextContent("no problems");
		await user.click(within(canvas).getByRole("button", { name: "Round 1" }));
		expect(loopAgent(canvas, "Checks")).toHaveTextContent("1 problem");
	});

	it("labels the way back with what the Critic decided", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);

		expect(loopDiagram(canvas)).toHaveTextContent("accepted ✓");
		await user.click(within(canvas).getByRole("button", { name: "Round 1" }));
		expect(loopDiagram(canvas)).toHaveTextContent("asked for 1 search");
		expect(loopDiagram(canvas)).not.toHaveTextContent("accepted");
	});

	it("says when the checks overruled the Critic's acceptance", async () => {
		const { api, path } = briefed();
		const critic = api.reviews.briefs[0]?.trace.steps.at(-1);
		if (critic?.output != null) {
			critic.output["overruled_by_checks"] = true;
		}
		const { canvas } = await openTrace(path);

		expect(loopDiagram(canvas)).toHaveTextContent(
			"accepted · overruled by the checks",
		);
		expect(loopDiagram(canvas)).not.toHaveTextContent("✓");
	});

	it("opens a step to its detail: the plan, the searches, the checks' problems, the Critic's requests", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);

		await user.click(
			within(canvas).getByRole("button", { name: "Before the first draft" }),
		);
		await user.click(
			within(canvas).getByRole("button", { name: "Planner · 1 call · 41 s" }),
		);
		expect(canvas).toHaveTextContent(
			"Decision: Choose between cash, vouchers or in-kind food",
		);
		expect(canvas).toHaveTextContent(
			"Options: Cash transfers · Vouchers · In-kind food",
		);

		await user.click(within(canvas).getByRole("button", { name: "Round 1" }));
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
		expect(canvas).toHaveTextContent("20,000 tokens in, 2,000 out");
	});

	it("ends with the totals and what the run used", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(canvas).toHaveTextContent(
			"Model: qwen3.5:9b · 9 calls · 61,200 tokens in, 8,900 out",
		);
		expect(used(canvas, "Round limit")).toHaveTextContent("3");
		const retriever = within(used(canvas, "Retriever")).getByText("bm25");
		expect(retriever.tagName).toBe("CODE");
	});

	it("sets what the run recorded apart from the names, one line per role", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const models = used(canvas, "Models");
		const writer = within(models).getByText("Writer");
		expect(writer.tagName).not.toBe("CODE");
		expect(within(models).getByText("qwen3.5:9b").tagName).toBe("CODE");
		expect(models).not.toHaveTextContent(",");
		const prompts = used(canvas, "Prompt versions");
		expect(within(prompts).getByText("brief.writer@2").tagName).toBe("CODE");
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

		expect(used(canvas, "Masking")).toHaveTextContent(
			"Off for this run (the owner's choice)",
		);
	});

	it("says masking was on when it was", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(used(canvas, "Masking")).toHaveTextContent(/^On$/);
	});

	it("shows the project's instructions as the Writer received them", async () => {
		const { api, path } = briefed();
		const entry = api.reviews.briefs[0];
		if (entry !== undefined) {
			entry.trace.instructions = {
				version: 2,
				text: "Write for [PERSON_1]'s team: short sentences.",
			};
		}
		const { canvas } = await openTrace(path);

		const section = within(canvas).getByRole("region", {
			name: "Instructions from the project team",
		});
		expect(section).toHaveTextContent(
			"Version 2, as the Writer received them, masked where masking applies.",
		);
		expect(section).toHaveTextContent("short sentences.");
		expect(section).not.toHaveTextContent("[PERSON_1]");
		expect(section).toHaveTextContent("Person 1");
	});

	it("says when the Writer had no instructions from the project team", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(
			within(canvas).getByRole("region", {
				name: "Instructions from the project team",
			}),
		).toHaveTextContent("None. The Writer had only the built-in instructions.");
	});

	it("draws the loop live while the brief is still being written", async () => {
		const { path, run } = briefed(false, "running");
		const { user } = renderApp(`${path}/trace`);
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
			source.emit("agent.step", 3, {
				round: 0,
				role: "retriever",
				status: "ok",
				seconds: 1,
				queries: ["cash transfers prices"],
				passages_new: 4,
				passages_total: 4,
			});
			source.emit("agent.step", 4, {
				round: 1,
				role: "writer",
				status: "ok",
				seconds: 90,
				output: { options: ["Cash", "Vouchers"], claims: 6 },
			});
		});

		await screen.findByText("Still writing. Steps appear as they finish.");
		const canvas = screen.getByRole("region", {
			name: "How this brief was made",
		});
		expect(
			within(canvas).getByRole("button", { name: "Round 1" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(loopAgent(canvas, "Writer")).toHaveTextContent(
			"2 options · 6 claims · 2 min",
		);
		expect(loopAgent(canvas, "Checks")).toHaveTextContent("running");
		expect(loopAgent(canvas, "Critic")).toHaveTextContent("not reached yet");
		const rows = within(
			within(canvas).getByRole("list", { name: "Round 1" }),
		).getAllByRole("listitem");
		expect(rows.map((row) => row.textContent)).toEqual([
			expect.stringContaining("Writer · 2 options · 6 claims · 2 min"),
			expect.stringContaining("Checks · running"),
		]);

		act(() => {
			source.emit("agent.step", 5, {
				round: 1,
				role: "checks",
				status: "ok",
				seconds: 0,
				problems: [],
			});
		});
		expect(loopAgent(canvas, "Checks")).toHaveTextContent("no problems");
		expect(loopAgent(canvas, "Critic")).toHaveTextContent("running");

		await user.click(
			within(canvas).getByRole("button", { name: "Before the first draft" }),
		);
		expect(loopAgent(canvas, "Retriever")).toHaveTextContent(
			"1 search · 4 new passages (4 in all)",
		);
		await user.click(
			within(canvas).getByRole("button", {
				name: "Retriever · 1 search · 4 new passages (4 in all)",
			}),
		);
		expect(canvas).toHaveTextContent("Searches: cash transfers prices");
	});

	it("has no accessibility violations while the brief is being written", async () => {
		const { path, run } = briefed(false, "running");
		const { container } = renderApp(`${path}/trace`);
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
		});
		await screen.findByText("Still writing. Steps appear as they finish.");

		await expectNoAccessibilityViolations(container);
	});

	it("has no accessibility violations", async () => {
		const { path } = briefed();
		const { container } = await openTrace(path);

		await expectNoAccessibilityViolations(container);
	});
});
