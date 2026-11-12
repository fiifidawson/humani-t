import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function reviewWithResults(role: "owner" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	api.reviews.addPaper(review, run, {
		title: "Cash in Mali",
		tier: "highly_relevant",
	});
	api.reviews.addBrief(review, run, {});
	return { review, run, path: `/p/${project.id}/r/${review.id}` };
}

async function openDownloads(
	user: ReturnType<typeof renderApp>["user"],
	canvasName: string | RegExp,
) {
	// The panel is drawn again once its data arrives ("Loading…" first), so it is
	// looked up afresh until its Download button is there.
	const button = await waitFor(() =>
		within(screen.getByRole("region", { name: canvasName })).getByRole(
			"button",
			{
				name: "Download",
			},
		),
	);
	await user.click(button);
	return within(screen.getByRole("region", { name: canvasName })).getByRole(
		"list",
		{
			name: "Downloads",
		},
	);
}

function links(list: HTMLElement) {
	return within(list)
		.getAllByRole("link")
		.map((link) => [link.textContent, link.getAttribute("href")]);
}

describe("downloads (UI_UX_SPEC §6.8, §6.9; API exports)", () => {
	it("offers the papers, the reference list, PRISMA and the run record from the papers panel", async () => {
		const { review, run, path } = reviewWithResults();
		const { user } = renderApp(`${path}/papers`);

		const list = await openDownloads(user, "Papers · run 1");

		const at = (name: string) =>
			`/api/v1/reviews/${review.id}/exports/${name}?run_id=${run.id}`;
		expect(links(list)).toEqual([
			["Papers (CSV)", at("dataset.csv")],
			["Papers (JSON lines)", at("dataset.jsonl")],
			["Included papers for a reference manager (RIS)", at("included.ris")],
			["PRISMA report", at("prisma.md")],
			["Run record", at("manifest.json")],
		]);
	});

	it("offers the brief as text, to print or save as PDF, and as data", async () => {
		const { review, run, path } = reviewWithResults();
		const { user } = renderApp(`${path}/brief`);

		const list = await openDownloads(user, "Decision brief");

		const at = (name: string) =>
			`/api/v1/reviews/${review.id}/exports/${name}?run_id=${run.id}`;
		expect(links(list)).toEqual([
			["Brief (Markdown)", at("brief.md")],
			["Brief to print or save as PDF", at("brief.html")],
			["Brief (JSON)", at("brief.json")],
		]);
		const printable = within(list).getByRole("link", {
			name: "Brief to print or save as PDF",
		});
		expect(printable).toHaveAttribute("target", "_blank");
		expect(printable).toHaveAttribute("rel", "noopener noreferrer");
	});

	it("lets every member download what they may read, viewers included", async () => {
		const { path } = reviewWithResults("viewer");
		const { user } = renderApp(`${path}/papers`);

		const list = await openDownloads(user, "Papers · run 1");

		expect(within(list).getAllByRole("link")).toHaveLength(5);
	});

	it("has no accessibility violations with the list open", async () => {
		const { path } = reviewWithResults();
		const { container, user } = renderApp(`${path}/papers`);
		await openDownloads(user, "Papers · run 1");

		await expectNoAccessibilityViolations(container);
	});
});
