import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { anyTime, roundEntries } from "../../test/agentTimeline";
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

	it("lists every round, oldest first, under its heading, with no picker and no diagram", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		expect(
			within(canvas)
				.getAllByRole("heading", { level: 4 })
				.map((heading) => heading.textContent),
		).toEqual(["Before the first draft", "Round 1", "Round 2"]);
		expect(roundEntries(canvas, "Round 1")).toHaveLength(3);
		expect(
			within(canvas).queryByRole("group", { name: "Round" }),
		).not.toBeInTheDocument();
		expect(canvas.querySelector("[data-node]")).toBeNull();
	});

	it("says in a sentence what each agent did, what it found and how long it took", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const [planner, retriever] = roundEntries(canvas, "Before the first draft");
		expect(planner).toHaveTextContent("Planner set the plan");
		expect(planner).toHaveTextContent(
			"3 options · 2 criteria · 2 sub-questions",
		);
		expect(planner).toHaveTextContent("41 s");
		expect(retriever).toHaveTextContent("Retriever searched the papers");
		expect(retriever).toHaveTextContent(
			"2 searches · 5 new passages (5 in all)",
		);

		const [writer, checks, critic] = roundEntries(canvas, "Round 1");
		expect(writer).toHaveTextContent("Writer drafted the brief");
		expect(writer).toHaveTextContent("3 options · 14 claims, 10 from sources");
		expect(writer).toHaveTextContent("2 min");
		expect(checks).toHaveTextContent("Checks found 1 problem");
		expect(checks).toHaveTextContent("In-kind food has no sourced benefit.");
		expect(critic).toHaveTextContent(
			"Critic asked for a revision and 1 more search",
		);
		expect(critic).toHaveTextContent(
			"Price effects in small markets are thin.",
		);
		expect(critic).toHaveTextContent("30 s");
	});

	it("leaves out a time under a second rather than showing 0 s", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const checks = roundEntries(canvas, "Round 1")[1];
		expect(checks?.textContent).not.toMatch(anyTime);
	});

	it("says a failure in words, with its short error, not colour alone", async () => {
		const { path } = briefed();
		const { canvas } = await openTrace(path);

		const writer = roundEntries(canvas, "Round 2")[0];
		expect(writer).toHaveTextContent("Writer failed");
		expect(writer).toHaveTextContent("The model server isn't answering.");
	});

	it("names a revision, the Critic's searches and an acceptance for what they are", async () => {
		const { api, path } = briefed();
		const steps = api.reviews.briefs[0]?.trace.steps ?? [];
		steps.splice(
			5,
			1,
			{
				round: 1,
				role: "retriever",
				status: "ok",
				seconds: 2,
				queries: ["price effects of cash in small markets"],
				passages_new: 3,
				passages_total: 8,
			},
			{
				round: 2,
				role: "writer",
				status: "ok",
				seconds: 100,
				output: { options: ["Cash transfers", "Vouchers"], claims: 12 },
			},
		);
		const { canvas } = await openTrace(path);

		expect(roundEntries(canvas, "Round 1")[3]).toHaveTextContent(
			"Retriever ran the Critic's searches",
		);
		const [writer, critic] = roundEntries(canvas, "Round 2");
		expect(writer).toHaveTextContent("Writer revised the brief");
		expect(critic).toHaveTextContent("Critic accepted the draft");
	});

	it("says when the checks overruled the Critic's acceptance", async () => {
		const { api, path } = briefed();
		const critic = api.reviews.briefs[0]?.trace.steps.at(-1);
		if (critic?.output != null) {
			critic.output["overruled_by_checks"] = true;
		}
		const { canvas } = await openTrace(path);

		expect(roundEntries(canvas, "Round 2")[1]).toHaveTextContent(
			"Critic accepted the draft, but the checks overruled it",
		);
	});

	it("opens a step's details in place: the plan, the Critic's requests, the tokens", async () => {
		const { path } = briefed();
		const { canvas, user } = await openTrace(path);

		const planner = within(canvas).getByRole("button", {
			name: "Details: Planner set the plan",
		});
		expect(planner).toHaveAttribute("aria-expanded", "false");
		await user.click(planner);
		expect(planner).toHaveAttribute("aria-expanded", "true");
		expect(canvas).toHaveTextContent(
			"Decision: Choose between cash, vouchers or in-kind food",
		);
		expect(canvas).toHaveTextContent(
			"Options: Cash transfers · Vouchers · In-kind food",
		);

		await user.click(
			within(canvas).getByRole("button", {
				name: "Details: Critic asked for a revision and 1 more search",
			}),
		);
		expect(canvas).toHaveTextContent(
			"Asked for: price effects of cash in small markets",
		);
		expect(canvas).toHaveTextContent("20,000 tokens in, 2,000 out");
	});

	it("offers no details where there is nothing more to show", async () => {
		const { api, path } = briefed();
		api.reviews.briefs[0]?.trace.steps.push({
			round: 2,
			role: "checks",
			status: "ok",
			seconds: 0,
			problems: [],
		});
		const { canvas } = await openTrace(path);

		const checks = roundEntries(canvas, "Round 2")[2] as HTMLElement;
		expect(checks).toHaveTextContent("Checks found no problems");
		expect(within(checks).queryByRole("button")).not.toBeInTheDocument();
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

	it("adds each step live, the one being run last, and nothing not reached yet", async () => {
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
			roundEntries(canvas, "Round 1").map((entry) => entry.textContent),
		).toEqual([
			expect.stringContaining("Writer drafted the brief"),
			expect.stringContaining("Checks are running"),
		]);
		expect(roundEntries(canvas, "Round 1")[0]).toHaveTextContent(
			"2 options · 6 claims",
		);

		act(() => {
			source.emit("agent.step", 5, {
				round: 1,
				role: "checks",
				status: "ok",
				seconds: 0,
				problems: [],
			});
		});
		expect(
			roundEntries(canvas, "Round 1").map((entry) => entry.textContent),
		).toEqual([
			expect.stringContaining("Writer drafted the brief"),
			expect.stringContaining("Checks found no problems"),
			expect.stringContaining("Critic is reviewing the draft"),
		]);

		await user.click(
			within(canvas).getByRole("button", {
				name: "Details: Retriever searched the papers",
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
