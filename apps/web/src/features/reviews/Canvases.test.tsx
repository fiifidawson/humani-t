import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function planned(role: "owner" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	return { api, project, review, path: `/p/${project.id}/r/${review.id}` };
}

beforeEach(() => {
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

describe("the search draft canvas (UI_UX_SPEC §6.5)", () => {
	it("opens from the draft card and explains how the search reads", async () => {
		const { path, project, review } = planned();
		const { user, router } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		await user.click(within(card).getByRole("link", { name: "Open draft" }));

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}/draft`),
		);
		const canvas = await screen.findByRole("region", {
			name: "Search draft · version 1 (not accepted yet)",
		});
		expect(canvas).toHaveTextContent(
			"Your question: Cash or vouchers in camps?",
		);
		expect(canvas).toHaveTextContent(
			"A paper must match at least one concept in every category. The words in a concept are alternatives (OR).",
		);
		expect(
			within(canvas).getByRole("heading", {
				name: "Category 1 · intervention",
			}),
		).toBeInTheDocument();
		expect(canvas).toHaveTextContent("cash transfers or: cash assistance");
		expect(canvas).toHaveTextContent('"prices" and "price changes" merged');
		const kept = within(canvas).getByRole("list", {
			name: "A paper is included only if it meets all of these",
		});
		expect(kept).toHaveTextContent("Displaced households or camps");
		expect(kept).toHaveTextContent("Reports a food security outcome");
		const left = within(canvas).getByRole("list", {
			name: "A paper is excluded if any of these apply",
		});
		expect(left).toHaveTextContent("School feeding programmes");
		expect(canvas).not.toHaveTextContent(/\b[IE]\d\b/);
	});

	it("offers changes as a button that takes the owner to the chat box", async () => {
		const { path } = planned();
		const { user } = renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", { name: /Search draft/ });

		await user.click(
			within(canvas).getByRole("button", {
				name: "Ask for changes in the chat",
			}),
		);

		expect(
			screen.getByRole("textbox", { name: "Changing the search · draft 1" }),
		).toHaveFocus();
	});

	it("marks the terms a person added, and shows what changed from the draft before", async () => {
		const { api, path, review } = planned();
		const plan = api.reviews.plans.get(review.id);
		if (plan === undefined) {
			throw new Error("no plan");
		}
		const { user } = renderApp(path);
		const box = await screen.findByRole("textbox", {
			name: "Changing the search · draft 1",
		});
		await user.type(box, "Add mobile money.{Enter}");
		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 2",
		});

		await user.click(within(card).getByRole("link", { name: "Open draft" }));

		const canvas = await screen.findByRole("region", {
			name: "Search draft · version 2 (not accepted yet)",
		});
		expect(within(canvas).getByText("added by you")).toBeInTheDocument();
		const changes = await within(canvas).findByRole("list", {
			name: "Changes from draft 1",
		});
		expect(changes).toHaveTextContent("+ added mobile money (cash transfers)");
	});

	it("accepts from the canvas, then turns read-only", async () => {
		const { api, path, review } = planned();
		const { user } = renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", {
			name: "Search draft · version 1 (not accepted yet)",
		});

		await user.click(
			within(canvas).getByRole("button", { name: "Accept and search" }),
		);

		expect(
			await screen.findByRole("region", {
				name: "Search draft · version 1 · accepted",
			}),
		).toBeInTheDocument();
		expect(api.requestsTo("POST", `/reviews/${review.id}/runs`)).toHaveLength(
			1,
		);
		expect(
			screen.queryByRole("button", { name: "Accept and search" }),
		).not.toBeInTheDocument();
	});

	it("closes with its close button", async () => {
		const { path, project, review } = planned();
		const { user, router } = renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", { name: /Search draft/ });

		await user.click(within(canvas).getByRole("button", { name: "Close" }));

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}`),
		);
	});

	it("has no accessibility violations", async () => {
		const { path } = planned();
		const { container } = renderApp(`${path}/draft`);
		await screen.findByRole("region", { name: /Search draft/ });

		await expectNoAccessibilityViolations(container);
	});
});

function screenedReview(role: "owner" | "viewer" = "owner") {
	const setup = planned(role);
	const { api, review } = setup;
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	api.reviews.addPaper(review, run, {
		title: "Cash transfers and food prices in Mali",
		tier: "highly_relevant",
		year: 2023,
		maskedText: "Households in [LOCATION_1] received cash.",
	});
	api.reviews.addPaper(review, run, {
		title: "Vouchers in Niger",
		tier: "relevant",
	});
	api.reviews.addPaper(review, run, {
		title: "School meals in Chad",
		tier: "excluded",
	});
	api.reviews.addPaper(review, run, {
		title: "A paper with no abstract",
		tier: null,
	});
	return { ...setup, run };
}

describe("the papers canvas (UI_UX_SPEC §6.8)", () => {
	it("lists the papers as a table, most relevant first, with label words", async () => {
		const { path } = screenedReview();
		renderApp(`${path}/papers`);

		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});
		const table = await within(canvas).findByRole("table");
		const headers = within(table)
			.getAllByRole("columnheader")
			.map((h) => h.textContent);
		expect(headers).toEqual(["Label", "Title", "Year", "Source"]);
		const rows = within(table).getAllByRole("row").slice(1);
		expect(rows[0]).toHaveTextContent("Highly relevant");
		expect(rows[0]).toHaveTextContent("Cash transfers and food prices in Mali");
		expect(rows.at(-1)).toHaveTextContent("Not screened");
	});

	it("filters by label, with each filter's count", async () => {
		const { api, path, review } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", { name: "Excluded 1" }),
		);

		await waitFor(() =>
			expect(within(canvas).getAllByRole("row")).toHaveLength(2),
		);
		expect(
			within(canvas).getByRole("button", { name: "Excluded 1" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			api.requests.some(
				(r) =>
					r.path === `/api/v1/reviews/${review.id}/papers` &&
					r.method === "GET",
			),
		).toBe(true);
	});

	it("opens a row to show why: the reason, the criteria met, what was read, and the model", async () => {
		const { path } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		const open = await within(canvas).findByRole("button", {
			name: "Cash transfers and food prices in Mali",
		});
		await user.click(open);

		expect(open).toHaveAttribute("aria-expanded", "true");
		const details = within(canvas).getByRole("region", {
			name: "Why: Cash transfers and food prices in Mali",
		});
		expect(details).toHaveTextContent(
			"Reports food security for displaced households.",
		);
		const met = within(details).getByRole("list", { name: "Criteria met" });
		expect(met).toHaveTextContent("Displaced households or camps");
		expect(met).toHaveTextContent("Reports a food security outcome");
		expect(details).not.toHaveTextContent(/\b[IE]\d\b/);
		expect(details).toHaveTextContent("Read: the abstract");
		expect(details).toHaveTextContent("Decided by llama3.1:8b");
		expect(details).toHaveTextContent("Source tag P1");

		await user.click(
			within(details).getByRole("button", {
				name: "Show the text that was screened",
			}),
		);
		expect(
			await within(details).findByText("masked location 1"),
		).toBeInTheDocument();
	});

	it("says in words why a paper was excluded, apart from the criteria it missed", async () => {
		const { path } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", {
				name: "School meals in Chad",
			}),
		);

		const details = within(canvas).getByRole("region", {
			name: "Why: School meals in Chad",
		});
		const reasons = within(details).getByRole("list", {
			name: "Excluded because",
		});
		const items = within(reasons).getAllByRole("listitem");
		expect(items[0]).toHaveTextContent("School feeding programmes");
		expect(items[1]).toHaveTextContent(
			"Doesn't meet: Reports a food security outcome",
		);
		expect(details).not.toHaveTextContent(/\b[IE]\d\b/);
	});

	it("opens a row to each criterion's answer and quote, the label, what was read, and where the brief cites it", async () => {
		const { api, path, review, run } = screenedReview();
		api.reviews.addPaper(review, run, {
			title: "Mobile money in displacement settings",
			tier: "highly_relevant",
			url: "https://example.org/mobile-money",
			isCitedInBrief: true,
			answers: [
				{ id: "I1", answer: "yes", quote: "internally displaced households" },
				{ id: "I2", answer: "partly", quote: "<b>food</b> consumption score" },
				{ id: "E1", answer: "no", quote: null },
			],
		});
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", {
				name: "Mobile money in displacement settings",
			}),
		);

		const details = within(canvas).getByRole("region", {
			name: "Why: Mobile money in displacement settings",
		});
		const answers = within(details).getByRole("list", {
			name: "Each criterion",
		});
		const items = within(answers).getAllByRole("listitem");
		expect(items[0]).toHaveTextContent(
			"Displaced households or camps · Yes“internally displaced households”",
		);
		expect(items[1]).toHaveTextContent(
			"Reports a food security outcome · Partly“<b>food</b> consumption score”",
		);
		expect(items[2]).toHaveTextContent(
			"Excluded if: School feeding programmes · No",
		);
		expect(details.querySelector("b")).toBeNull();
		expect(details).not.toHaveTextContent(/\b[IE]\d\b/);
		expect(details).toHaveTextContent(
			"Label: Highly relevant, as the model judged it. No rule changed it.",
		);
		expect(details).toHaveTextContent(
			"Read: the abstract only, not the full text",
		);
		expect(
			within(details).getByRole("link", { name: "Open paper" }),
		).toHaveAttribute("href", "https://example.org/mobile-money");
		expect(details).toHaveTextContent("Cited in the brief as [P5]");
	});

	it("doesn't say a paper is cited when the brief doesn't cite it", async () => {
		const { path } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", { name: "Vouchers in Niger" }),
		);

		const details = within(canvas).getByRole("region", {
			name: "Why: Vouchers in Niger",
		});
		expect(details).not.toHaveTextContent("Cited in the brief");
	});

	it("filters to skipped and failed papers, and says in each row why", async () => {
		const { api, path, review, run } = screenedReview();
		api.reviews.addPaper(review, run, {
			title: "A paper not masked yet",
			tier: null,
			status: "skipped",
			skippedBecause: "not_masked",
		});
		api.reviews.addPaper(review, run, {
			title: "A paper with only a title",
			tier: null,
			status: "skipped",
			skippedBecause: "no_abstract",
		});
		api.reviews.addPaper(review, run, {
			title: "A paper the privacy check stopped",
			tier: null,
			status: "failed",
		});
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", { name: "Skipped 2" }),
		);

		await waitFor(() =>
			expect(within(canvas).getAllByRole("row")).toHaveLength(3),
		);
		const skipped = within(canvas).getAllByRole("row").slice(1);
		expect(skipped[0]).toHaveTextContent("Skipped");
		expect(skipped[0]).toHaveTextContent(
			"Its text wasn't masked, so it wasn't sent to the model.",
		);
		expect(skipped[1]).toHaveTextContent(
			"It has no abstract, so there was nothing to screen.",
		);

		await user.click(within(canvas).getByRole("button", { name: "Failed 1" }));

		await waitFor(() =>
			expect(within(canvas).getAllByRole("row")).toHaveLength(2),
		);
		const failed = within(canvas).getAllByRole("row")[1];
		expect(failed).toHaveTextContent("Failed");
		expect(failed).toHaveTextContent(
			"The privacy check stopped its text, so it wasn't sent to the model.",
		);
		expect(
			within(canvas).getByRole("button", { name: "Failed 1" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			within(canvas).getByRole("button", { name: "Not screened 1" }),
		).toBeInTheDocument();
	});

	it("counts every paper once in All", async () => {
		const { api, path, review, run } = screenedReview();
		api.reviews.addPaper(review, run, {
			title: "A paper the privacy check stopped",
			tier: null,
			status: "failed",
		});
		renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		expect(
			await within(canvas).findByRole("button", { name: "All 5" }),
		).toBeInTheDocument();
	});

	it("opens on the PRISMA flow, from what was found to what was included", async () => {
		const { path } = screenedReview();
		renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		const flow = await within(canvas).findByRole("list", {
			name: "PRISMA flow",
		});
		const steps = within(flow).getAllByRole("listitem");
		expect(
			steps.map((step) => step.querySelector("[data-step]")?.textContent),
		).toEqual(["Identified7", "Unique4", "Screened3", "Included2"]);
		const filters = within(canvas).getByRole("group", { name: "Show" });
		expect(
			flow.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
	});

	it("filters the table from a step of the flow", async () => {
		const { path } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});
		const flow = await within(canvas).findByRole("list", {
			name: "PRISMA flow",
		});

		await user.click(within(flow).getByRole("button", { name: /excluded/ }));

		await waitFor(() =>
			expect(
				within(canvas).getByRole("button", { name: "Excluded 1" }),
			).toHaveAttribute("aria-pressed", "true"),
		);

		await user.click(within(flow).getByRole("button", { name: /^Included/ }));

		await waitFor(() =>
			expect(
				within(canvas).getByRole("button", { name: "Included 2" }),
			).toHaveAttribute("aria-pressed", "true"),
		);
	});

	it("has no accessibility violations", async () => {
		const { path } = screenedReview();
		const { container } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});
		await within(canvas).findByRole("table");

		await expectNoAccessibilityViolations(container);
	});
});
