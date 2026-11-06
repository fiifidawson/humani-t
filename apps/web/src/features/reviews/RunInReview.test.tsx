import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function runningReview(
	role: "owner" | "reviewer" | "viewer" = "owner",
	state: "running" | "failed" = "running",
) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, state);
	return { api, project, review, run, path: `/p/${project.id}/r/${review.id}` };
}

async function stream(runId: string): Promise<FakeEventSource> {
	const source = await waitFor(() => {
		const found = FakeEventSource.instances.find((s) =>
			s.url.startsWith(`/api/v1/runs/${runId}/events`),
		);
		expect(found).toBeDefined();
		return found as FakeEventSource;
	});
	act(() => source.open());
	return source;
}

function send(source: FakeEventSource, ...events: [string, unknown][]) {
	act(() => {
		for (const [type, data] of events) {
			source.emit(type, source.nextId, data);
			source.nextId += 1;
		}
	});
}

const at = "2026-11-03T10:51:00Z";
const started = (stage: string, position: number) =>
	["stage.started", { stage, position, total: 8, at }] as [string, unknown];
const finished = (stage: string, seconds: number, summary = {}) =>
	["stage.finished", { stage, seconds, summary }] as [string, unknown];
const screened = (done: number, item: string) =>
	["stage.progress", { stage: "screen", done, total: 3, item }] as [
		string,
		unknown,
	];

beforeEach(() => {
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

describe("the run strip in the chat (UI_UX_SPEC §5.3.5, §11.2)", () => {
	it("follows the latest run on one stream and updates one line in place", async () => {
		const { path, run } = runningReview();
		renderApp(path);
		const source = await stream(run.id);

		send(
			source,
			started("screen", 5),
			screened(1, "HIGH · Cash in Mali — reports prices"),
		);

		const card = await screen.findByRole("article", { name: "Run 1" });
		expect(card).toHaveTextContent("Screening · 1 of 3");
		expect(FakeEventSource.instances).toHaveLength(1);
	});

	it("opens to a list of stages, each with its state in words", async () => {
		const { path, run } = runningReview();
		const { user } = renderApp(path);
		const source = await stream(run.id);
		send(
			source,
			started("queries", 1),
			finished("queries", 2),
			started("discover", 2),
		);
		const card = await screen.findByRole("article", { name: "Run 1" });

		await user.click(within(card).getByRole("button", { name: "Show stages" }));

		const stages = within(card).getByRole("list", { name: "Stages" });
		const rows = within(stages).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent("Queries · Done · 2 s");
		expect(rows[1]).toHaveTextContent("Discover · Running");
		expect(rows[2]).toHaveTextContent("Ingest · Not started");
		expect(rows).toHaveLength(8);
		expect(
			within(card).getByRole("link", { name: "Open run details" }),
		).toBeInTheDocument();
	});

	it("asks once before cancelling, and says finished stages are kept", async () => {
		const { api, path, run } = runningReview();
		const { user } = renderApp(path);
		const source = await stream(run.id);
		send(source, started("discover", 2));
		const card = await screen.findByRole("article", { name: "Run 1" });
		await user.click(within(card).getByRole("button", { name: "Show stages" }));

		await user.click(within(card).getByRole("button", { name: "Cancel" }));
		const dialog = await screen.findByRole("dialog", {
			name: "Stop this run?",
		});
		expect(dialog).toHaveTextContent(
			"Finished stages are kept, and you can run the rest later.",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Stop the run" }),
		);

		await waitFor(() =>
			expect(api.requestsTo("POST", `/runs/${run.id}/cancel`)).toHaveLength(1),
		);
		send(source, [
			"job.finished",
			{ outcome: "cancelled", seconds: 30, error: null },
		]);
		expect(card).toHaveTextContent("Run cancelled at Discovering");
	});

	it("offers to try again from the stage that stopped", async () => {
		const { api, path, review, run } = runningReview();
		const { user } = renderApp(path);
		const source = await stream(run.id);
		send(source, started("screen", 5), [
			"job.finished",
			{
				outcome: "failed",
				seconds: 60,
				error: "The model server isn't answering.",
			},
		]);
		run.state = "failed";
		run.outcome = "failed";
		const card = await screen.findByRole("article", { name: "Run 1" });
		expect(card).toHaveTextContent(
			"Run stopped at Screening · The model server isn't answering.",
		);

		await user.click(within(card).getByRole("button", { name: "Show stages" }));
		await user.click(
			within(card).getByRole("button", { name: "Try again from this stage" }),
		);

		await waitFor(() =>
			expect(
				api.requestsTo("POST", `/reviews/${review.id}/runs`)[0]?.body,
			).toEqual({
				stages: ["screen", "export", "index", "answer"],
			}),
		);
	});

	it("shows no actions to a viewer", async () => {
		const { path, run } = runningReview("viewer");
		const { user } = renderApp(path);
		const source = await stream(run.id);
		send(source, started("discover", 2));
		const card = await screen.findByRole("article", { name: "Run 1" });

		await user.click(within(card).getByRole("button", { name: "Show stages" }));

		expect(
			within(card).queryByRole("button", { name: "Cancel" }),
		).not.toBeInTheDocument();
	});
});

describe("gates in the chat (UI_UX_SPEC §5.3.6, §6.6)", () => {
	it("asks to confirm the search terms, showing the query, and sends the decision", async () => {
		const { api, path, run } = runningReview("reviewer");
		const gateId = api.reviews.addGate(run, "vocabulary");
		const { user } = renderApp(path);
		const source = await stream(run.id);

		send(source, started("queries", 1), [
			"gate.required",
			{
				gate_id: gateId,
				kind: "vocabulary",
				payload: { rendered: '("cash" OR "vouchers")' },
			},
		]);

		const card = await screen.findByRole("article", {
			name: "Needs you · Confirm the search terms",
		});
		expect(card).toHaveTextContent('("cash" OR "vouchers")');
		await user.click(
			within(card).getByRole("button", { name: "Confirm and continue" }),
		);

		await waitFor(() =>
			expect(api.requestsTo("POST", `/gates/${gateId}`)[0]?.body).toEqual({
				decision: "approve",
			}),
		);
		send(source, [
			"gate.resolved",
			{ gate_id: gateId, by: "u-1", decision: "approve" },
		]);
		await waitFor(() =>
			expect(
				screen.queryByRole("article", {
					name: "Needs you · Confirm the search terms",
				}),
			).not.toBeInTheDocument(),
		);
	});

	it("tells a reviewer who can approve masking", async () => {
		const { api, path, run } = runningReview("reviewer");
		const gateId = api.reviews.addGate(run, "privacy_review");
		renderApp(path);
		const source = await stream(run.id);

		send(source, started("mask", 4), [
			"gate.required",
			{ gate_id: gateId, kind: "privacy_review", payload: {} },
		]);

		const card = await screen.findByRole("article", {
			name: "Needs you · Privacy review",
		});
		expect(card).toHaveTextContent("Only owners can do this.");
		expect(within(card).queryByRole("button")).not.toBeInTheDocument();
	});
});

describe("the screening summary (UI_UX_SPEC §5.3.7)", () => {
	it("is posted when Screen finishes, with each label as a word and a count", async () => {
		const { api, path, review, run } = runningReview();
		api.reviews.addPaper(review, run, {
			title: "Cash in Mali",
			tier: "highly_relevant",
		});
		api.reviews.addPaper(review, run, {
			title: "Vouchers in Niger",
			tier: "relevant",
		});
		api.reviews.addPaper(review, run, {
			title: "School meals",
			tier: "excluded",
		});
		api.reviews.addPaper(review, run, { title: "No abstract", tier: null });
		renderApp(path);
		const source = await stream(run.id);

		send(source, started("screen", 5), finished("screen", 120, { papers: 4 }));

		const card = await screen.findByRole("article", {
			name: "Screening · 4 papers",
		});
		await waitFor(() => expect(card).toHaveTextContent("1 Highly relevant"));
		expect(card).toHaveTextContent("1 Relevant");
		expect(card).toHaveTextContent("1 Excluded");
		expect(card).toHaveTextContent("1 Not screened");
		expect(
			within(card).getByRole("link", { name: "Open papers" }),
		).toBeInTheDocument();
	});
});

describe("the run canvas (UI_UX_SPEC §6.7)", () => {
	it("opens beside the chat with the stages and each screened paper, newest first", async () => {
		const { path, project, review, run } = runningReview();
		const { user, router } = renderApp(path);
		const source = await stream(run.id);
		send(
			source,
			started("screen", 5),
			screened(1, "HIGH · Cash in Mali — reports prices"),
			screened(2, "EXCLUDE · School meals — intervention not matched"),
		);
		const card = await screen.findByRole("article", { name: "Run 1" });
		await user.click(within(card).getByRole("button", { name: "Show stages" }));

		await user.click(
			within(card).getByRole("link", { name: "Open run details" }),
		);

		await waitFor(() =>
			expect(currentPath(router)).toBe(
				`/p/${project.id}/r/${review.id}/run/${run.id}`,
			),
		);
		const canvas = await screen.findByRole("region", { name: "Run 1" });
		const papers = within(canvas).getByRole("list", {
			name: "Screened papers",
		});
		const rows = within(papers).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent("Excluded");
		expect(rows[0]).toHaveTextContent("School meals");
		expect(rows[0]).toHaveTextContent("intervention not matched");
		expect(rows[1]).toHaveTextContent("Highly relevant");
		expect(
			within(canvas).getByRole("progressbar", { name: "Screen · 2 of 3" }),
		).toBeInTheDocument();
		expect(FakeEventSource.instances).toHaveLength(1);

		await user.click(within(canvas).getByRole("button", { name: "Relevant" }));
		expect(within(papers).getAllByRole("listitem")).toHaveLength(1);
	});

	it("closes with Esc and gives the chat back", async () => {
		const { path, project, review, run } = runningReview();
		const { user, router } = renderApp(`${path}/run/${run.id}`);
		await stream(run.id);
		await screen.findByRole("region", { name: "Run 1" });

		await user.keyboard("{Escape}");

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}`),
		);
		expect(
			screen.queryByRole("region", { name: "Run 1" }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path, run } = runningReview();
		const { container } = renderApp(`${path}/run/${run.id}`);
		const source = await stream(run.id);
		send(
			source,
			started("screen", 5),
			screened(1, "HIGH · Cash in Mali — reports prices"),
		);
		await screen.findByRole("region", { name: "Run 1" });

		await expectNoAccessibilityViolations(container);
	});
});
