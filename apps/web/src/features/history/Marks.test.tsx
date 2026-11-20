import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const TITLE = "Cash transfers and food prices in Mali";

function screened() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao");
	const other = api.addProject("Market assessment, Mopti");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	const paper = api.reviews.addPaper(review, run, {
		title: TITLE,
		tier: "highly_relevant",
		year: 2023,
	});
	return {
		api,
		project,
		other,
		review,
		paper,
		path: `/p/${project.id}/r/${review.id}`,
	};
}

async function papers() {
	return screen.findByRole("region", { name: "Papers · run 1" });
}

describe("seen marks (UI_UX_SPEC §7.5)", () => {
	it("marks a paper seen when its row is opened", async () => {
		const { api, path, project, paper } = screened();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await papers();
		expect(canvas).not.toHaveTextContent("Seen");

		await user.click(
			await within(canvas).findByRole("button", { name: TITLE }),
		);

		expect(await within(canvas).findByText("Seen")).toBeInTheDocument();
		expect(
			api.requests.some(
				(r) =>
					r.method === "PUT" &&
					r.path ===
						`/api/v1/projects/${project.id}/papers/${paper.paper_id}/seen`,
			),
		).toBe(true);
	});

	it("names the other projects it was seen in, as links", async () => {
		const { api, path, project, other, paper } = screened();
		api.history.setMarks(project.id, {
			paper_id: paper.paper_id,
			is_seen: true,
			is_saved: false,
			seen_in_other_projects: [{ id: other.id, name: other.name }],
		});
		renderApp(`${path}/papers`);
		const canvas = await papers();

		expect(
			await within(canvas).findByText(/in 1 other project/),
		).toBeInTheDocument();
		expect(
			within(canvas).getByRole("link", { name: "Market assessment, Mopti" }),
		).toHaveAttribute("href", `/p/${other.id}`);
	});

	it("marks a source seen when the brief's source panel opens it", async () => {
		const { api, project, review } = screened();
		const run = api.reviews.addRun(review, "done");
		api.reviews.addBrief(review, run);
		renderApp(`/p/${project.id}/r/${review.id}/brief?source=P1`);
		await screen.findByRole("complementary", { name: "P1" });

		await waitFor(() =>
			expect(
				api.requests.some(
					(r) =>
						r.method === "PUT" &&
						r.path.startsWith(`/api/v1/projects/${project.id}/papers/`) &&
						r.path.endsWith("/seen"),
				),
			).toBe(true),
		);
	});

	it("tells the API a brief was opened", async () => {
		const { api, project, review } = screened();
		const run = api.reviews.addRun(review, "done");
		const brief = api.reviews.addBrief(review, run);
		renderApp(`/p/${project.id}/r/${review.id}/brief`);

		await waitFor(() => expect(api.history.briefsSeen).toEqual([brief.id]));
	});
});

describe("saved papers (UI_UX_SPEC §7.5)", () => {
	it("saves a paper with its star, and lists it from the rail", async () => {
		const { path } = screened();
		const { user, router } = renderApp(`${path}/papers`);
		const canvas = await papers();
		const star = await within(canvas).findByRole("button", {
			name: `Save: ${TITLE}`,
		});

		await user.click(star);

		await waitFor(() => expect(star).toHaveAttribute("aria-pressed", "true"));
		const rail = screen.getByRole("navigation", { name: "Main" });
		await user.click(within(rail).getByRole("link", { name: "Saved papers" }));
		await waitFor(() => expect(currentPath(router)).toBe("/saved"));
		expect(
			await screen.findByRole("button", { name: /^Remove from saved: / }),
		).toBeInTheDocument();
	});

	it("takes a paper off the list", async () => {
		const { api, project } = screened();
		const saved = api.history.addSaved(project.id);
		const { user } = renderApp("/saved");

		await user.click(
			await screen.findByRole("button", {
				name: `Remove from saved: ${saved.title}`,
			}),
		);

		expect(await screen.findByText(/No saved papers yet/)).toBeInTheDocument();
	});

	it("links each saved paper to its project's papers", async () => {
		const { api, project } = screened();
		const saved = api.history.addSaved(project.id);
		renderApp("/saved");

		const main = await screen.findByRole("main");
		const link = await within(main).findByRole("link", {
			name: "Cash vs vouchers, Gao",
		});
		expect(link).toHaveAttribute(
			"href",
			`/p/${project.id}/r/${saved.review_id}/papers`,
		);
	});

	it("has no accessibility violations", async () => {
		const { api, project } = screened();
		api.history.addSaved(project.id);
		const { container } = renderApp("/saved");
		await screen.findByRole("heading", { name: "Saved papers", level: 1 });
		await screen.findByText(/Cash transfers and food prices in small/);

		await expectNoAccessibilityViolations(container);
	});
});
