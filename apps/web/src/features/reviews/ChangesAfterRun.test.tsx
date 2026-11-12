import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const EVERY_STAGE = [
	"queries",
	"discover",
	"ingest",
	"mask",
	"screen",
	"export",
	"answer",
];

function ranReview(state: "done" | "running" = "done") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "owner");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	api.reviews.addRun(review, state);
	return { api, review, path: `/p/${project.id}/r/${review.id}` };
}

async function askForChange(text: string, path: string) {
	const view = renderApp(path);
	const box = await screen.findByRole("textbox", { name: "Change the search" });
	await view.user.type(box, `${text}{Enter}`);
	const draft = await screen.findByRole("article", {
		name: "Search planner · Draft 2",
	});
	return { ...view, draft };
}

function stagesStarted(api: ReturnType<typeof fakeApi>, reviewId: string) {
	return api
		.requestsTo("POST", `/reviews/${reviewId}/runs`)
		.map((r) => (r.body as { stages: string[] }).stages);
}

describe("changing the search after a run (Wave 2, prompt-first)", () => {
	it("takes the change in the chat box and shows the next draft after the run", async () => {
		const { path } = ranReview();

		const { draft } = await askForChange(
			"Make it general: any refugees, not only Tigray",
			path,
		);

		const run = screen.getByRole("article", { name: "Run 1" });
		expect(
			run.compareDocumentPosition(draft) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(
			within(draft).getByRole("button", { name: "Accept and search again" }),
		).toBeInTheDocument();
	});

	it("searches again from the start when the change touched the search", async () => {
		const { api, review, path } = ranReview();
		const { user, draft } = await askForChange("Add mobile money", path);

		await user.click(
			within(draft).getByRole("button", { name: "Accept and search again" }),
		);

		await waitFor(() =>
			expect(stagesStarted(api, review.id)).toEqual([EVERY_STAGE]),
		);
	});

	it("re-screens the papers already found when only the criteria changed", async () => {
		const { api, review, path } = ranReview();
		api.reviews.nextChange = "criteria";
		const { user, draft } = await askForChange(
			"Only studies from 2015 onwards",
			path,
		);

		await user.click(
			within(draft).getByRole("button", { name: "Accept and re-screen" }),
		);

		await waitFor(() =>
			expect(stagesStarted(api, review.id)).toEqual([
				["screen", "export", "answer"],
			]),
		);
	});

	it("says a change waits while a run is going", async () => {
		const { path } = ranReview("running");
		renderApp(path);

		expect(
			await screen.findByText(
				"Wait for the run to finish, or cancel it, to change the search.",
			),
		).toBeInTheDocument();
	});
});
