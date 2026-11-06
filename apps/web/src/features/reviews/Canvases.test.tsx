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
		expect(canvas).toHaveTextContent(
			"I1 Include: Displaced households or camps",
		);
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
		expect(details).toHaveTextContent("I1 Displaced households or camps");
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

	it("shows the PRISMA counts as a table", async () => {
		const { path } = screenedReview();
		const { user } = renderApp(`${path}/papers`);
		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});

		await user.click(
			await within(canvas).findByRole("button", { name: "Show PRISMA counts" }),
		);

		const prisma = await within(canvas).findByRole("table", {
			name: "PRISMA counts",
		});
		expect(
			within(prisma).getByRole("row", { name: "Identified 7" }),
		).toBeInTheDocument();
		expect(
			within(prisma).getByRole("row", { name: "Included 2" }),
		).toBeInTheDocument();
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
