import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function withTimeline() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao");
	const h = api.history;
	const reviewId = "r-1";
	const run = h.event(project.id, {
		action: "run",
		kind: "run",
		review_id: reviewId,
		run_id: "run-3",
		run_number: 3,
		run_state: "done",
		seconds: 1620,
		model_ids: ["qwen3.5:9b"],
		stages: [
			{ stage: "discover", state: "done", seconds: 60 },
			{ stage: "screen", state: "done", seconds: 1500 },
		],
		at: "2026-11-03T10:51:00Z",
	});
	h.timelines.set(project.id, [
		{
			review: {
				id: reviewId,
				question: "Should we switch from in-kind food aid to cash?",
				started_at: "2026-11-03T10:42:00Z",
			},
			events: [
				h.event(project.id, {
					action: "brief_ready",
					kind: "brief",
					review_id: reviewId,
					version: 2,
					run_id: "run-3",
					model_ids: ["qwen3.5:9b"],
					at: "2026-11-03T10:58:00Z",
				}),
				run,
				h.event(project.id, {
					action: "plan_accepted",
					kind: "review",
					review_id: reviewId,
					version: 2,
					actor: { id: "u-1", display_name: "Amara N." },
					at: "2026-11-03T10:49:00Z",
				}),
			],
		},
		{
			review: {
				id: "r-2",
				question: "Do vouchers reduce protection risks?",
				started_at: "2026-10-28T09:00:00Z",
			},
			events: [
				h.event(project.id, {
					action: "review_started",
					kind: "review",
					review_id: "r-2",
					text: "Do vouchers reduce protection risks?",
					actor: { id: "u-1", display_name: "Amara N." },
					at: "2026-10-28T09:00:00Z",
				}),
			],
		},
	]);
	return { api, project, path: `/p/${project.id}/history` };
}

async function firstReview() {
	return screen.findByRole("region", {
		name: "Review: Should we switch from in-kind food aid to cash?",
	});
}

describe("the project timeline (UI_UX_SPEC §7.4)", () => {
	it("opens from the workspace header", async () => {
		const { project } = withTimeline();
		const { user, router } = renderApp(`/p/${project.id}/documents`);

		await user.click(await screen.findByRole("link", { name: "History" }));

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/history`),
		);
	});

	it("groups lines by review in the API's order, the latest review open", async () => {
		const { path } = withTimeline();
		renderApp(path);

		const review = await firstReview();
		const lines = within(review).getAllByRole("listitem");
		expect(lines.map((line) => line.textContent)).toEqual([
			expect.stringContaining("Brief v2 ready · qwen3.5:9b"),
			expect.stringContaining("Run 3 finished · 27 min · qwen3.5:9b"),
			expect.stringContaining("Search plan v2 accepted by Amara N."),
		]);
		expect(
			screen.getByRole("button", {
				name: "Review: Do vouchers reduce protection risks?",
			}),
		).toHaveAttribute("aria-expanded", "false");
	});

	it("gives each line one action, and opens a run to its stages", async () => {
		const { path, project } = withTimeline();
		const { user } = renderApp(path);
		const review = await firstReview();

		expect(
			within(review).getByRole("link", {
				name: "Run 3 finished · 27 min · qwen3.5:9b",
			}),
		).toHaveAttribute("href", `/p/${project.id}/r/r-1/run/run-3`);
		await user.click(
			within(review).getByRole("button", {
				name: "Stages of Run 3 finished · 27 min · qwen3.5:9b",
			}),
		);

		expect(review).toHaveTextContent("Screen · done · 25 min");
	});

	it("hides a kind of line with its Show filter, and remembers the choice", async () => {
		const { path } = withTimeline();
		const { user, unmount } = renderApp(path);
		const review = await firstReview();

		await user.click(screen.getByRole("button", { name: "Runs" }));

		expect(within(review).getAllByRole("listitem")).toHaveLength(2);
		unmount();
		renderApp(path);
		const again = await firstReview();
		expect(screen.getByRole("button", { name: "Runs" })).toHaveAttribute(
			"aria-pressed",
			"false",
		);
		expect(within(again).getAllByRole("listitem")).toHaveLength(2);
	});

	it("offers to compare two runs of the latest review at its foot", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		const project = api.addProject("Cash vs vouchers, Gao");
		const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
		api.reviews.startPlan(review, "shallow");
		api.reviews.accept(review);
		api.reviews.addRun(review, "done");
		api.reviews.addRun(review, "done");
		api.history.timelines.set(project.id, [
			{
				review: {
					id: review.id,
					question: "Cash or vouchers in camps?",
					started_at: "2026-11-03T10:42:00Z",
				},
				events: [],
			},
		]);
		renderApp(`/p/${project.id}/history`);

		expect(
			await screen.findByRole("group", { name: "Compare runs" }),
		).toBeInTheDocument();
	});

	it("says the project does not exist for someone who is not a member", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		renderApp("/p/p-missing/history");

		expect(
			await screen.findByRole("heading", { name: "Project not found" }),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path } = withTimeline();
		const { container } = renderApp(path);
		await firstReview();

		await expectNoAccessibilityViolations(container);
	});
});
