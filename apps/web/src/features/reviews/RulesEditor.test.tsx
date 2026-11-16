import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function screenedReview(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	api.reviews.addPaper(review, run, {
		title: "Cash transfers and food prices in Mali",
		tier: "highly_relevant",
	});
	api.reviews.addPaper(review, run, {
		title: "Vouchers in Niger",
		tier: "relevant",
	});
	api.reviews.addPaper(review, run, {
		title: "School meals in Chad",
		tier: "excluded",
	});
	api.reviews.rules.moves = [
		{ from_tier: "relevant", to_tier: "highly_relevant", count: 1 },
		{ from_tier: "excluded", to_tier: "possibly_relevant", count: 1 },
	];
	api.reviews.rules.untouchable = 3;
	return { api, review, path: `/p/${project.id}/r/${review.id}` };
}

async function openRules(path: string) {
	const view = renderApp(`${path}/papers`);
	const canvas = await screen.findByRole("region", { name: "Papers · run 1" });
	await view.user.click(
		await within(canvas).findByRole("button", { name: "Edit rules" }),
	);
	const form = await within(canvas).findByRole("form", { name: "Label rules" });
	return { ...view, canvas, form };
}

function rowOf(canvas: HTMLElement, title: string) {
	const row = within(canvas).getByText(title).closest("tr");
	if (row === null) {
		throw new Error(`no row for ${title}`);
	}
	return row;
}

describe("editing the label rules (UI_UX_SPEC §6.8)", () => {
	it("offers Edit rules to owners and reviewers", async () => {
		for (const role of ["owner", "reviewer"] as const) {
			const { path } = screenedReview(role);
			const { unmount } = renderApp(`${path}/papers`);
			const canvas = await screen.findByRole("region", {
				name: "Papers · run 1",
			});
			expect(
				await within(canvas).findByRole("button", { name: "Edit rules" }),
			).toBeInTheDocument();
			unmount();
		}
	});

	it("does not offer Edit rules to a viewer", async () => {
		const { path } = screenedReview("viewer");
		renderApp(`${path}/papers`);

		const canvas = await screen.findByRole("region", {
			name: "Papers · run 1",
		});
		await within(canvas).findByRole("table");

		expect(
			within(canvas).queryByRole("button", { name: "Edit rules" }),
		).not.toBeInTheDocument();
	});

	it("opens with the rules in force, or the defaults when there are none", async () => {
		const { form } = await openRules(screenedReview().path);

		expect(
			within(form).getByRole("spinbutton", {
				name: "Possibly relevant needs at least this many inclusion criteria met",
			}),
		).toHaveValue(1);
		expect(within(form).getByRole("radio", { name: "Not met" })).toBeChecked();
	});

	it("previews how many papers would move, and which can't be relabelled, before anything changes", async () => {
		const { api, review, path } = screenedReview();
		const { user, form } = await openRules(path);

		await user.click(within(form).getByRole("button", { name: "Preview" }));

		expect(
			await within(form).findByText(
				"1 would move from Relevant to Highly relevant",
			),
		).toBeInTheDocument();
		expect(
			within(form).getByText("1 would move from Excluded to Possibly relevant"),
		).toBeInTheDocument();
		expect(
			within(form).getByText(
				"3 papers can't be relabelled (no per-criterion answers)",
			),
		).toBeInTheDocument();
		expect(
			api.requestsTo("PUT", `/reviews/${review.id}/label-rules`),
		).toHaveLength(0);
	});

	it("sends the rules as chosen", async () => {
		const { api, review, path } = screenedReview();
		const { user, form } = await openRules(path);
		const count = within(form).getByRole("spinbutton");

		await user.clear(count);
		await user.type(count, "2");
		await user.click(within(form).getByRole("radio", { name: /Uncertain/ }));
		await user.click(within(form).getByRole("button", { name: "Preview" }));
		await user.click(
			await within(form).findByRole("button", { name: "Apply" }),
		);

		await waitFor(() =>
			expect(
				api
					.requestsTo("PUT", `/reviews/${review.id}/label-rules`)
					.map((r) => r.body),
			).toEqual([{ possibly_min_met: 2, unclear_counts_as: "uncertain" }]),
		);
	});

	it("asks for a preview again once a rule is changed", async () => {
		const { path } = screenedReview();
		const { user, form } = await openRules(path);
		await user.click(within(form).getByRole("button", { name: "Preview" }));
		await within(form).findByRole("button", { name: "Apply" });

		await user.click(within(form).getByRole("radio", { name: "Met" }));

		expect(
			within(form).queryByRole("button", { name: "Apply" }),
		).not.toBeInTheDocument();
	});

	it("changes the table only once the API has applied the rules", async () => {
		const { api, path } = screenedReview();
		let release: () => void = () => undefined;
		api.reviews.rules.applyReply = new Promise((resolve) => {
			release = resolve;
		});
		const { user, canvas, form } = await openRules(path);
		await user.click(within(form).getByRole("button", { name: "Preview" }));
		await user.click(
			await within(form).findByRole("button", { name: "Apply" }),
		);

		expect(
			within(form).getByRole("button", { name: "Applying…" }),
		).toBeDisabled();
		expect(rowOf(canvas, "Vouchers in Niger")).toHaveTextContent("Relevant");

		release();

		await waitFor(() =>
			expect(rowOf(canvas, "Vouchers in Niger")).toHaveTextContent(
				"Highly relevant",
			),
		);
		expect(
			within(canvas).getByText("Rules applied: 2 papers moved."),
		).toBeInTheDocument();
	});

	it("posts a note in the chat when the rules change", async () => {
		const { path } = screenedReview();
		const { user, form } = await openRules(path);
		await user.click(within(form).getByRole("button", { name: "Preview" }));
		await user.click(
			await within(form).findByRole("button", { name: "Apply" }),
		);

		const chat = screen.getByRole("log", { name: "Project conversation" });
		expect(
			await within(chat).findByText(
				"Labels changed by new rules: 2 papers moved",
			),
		).toBeInTheDocument();
	});

	it("shows earlier rule changes in the chat to every member", async () => {
		const { api, review, path } = screenedReview("viewer");
		api.reviews.rules.add(review.id, { papers_moved: 4 });
		renderApp(path);

		const chat = await screen.findByRole("log", {
			name: "Project conversation",
		});
		expect(
			await within(chat).findByText(
				"Labels changed by new rules: 4 papers moved",
			),
		).toBeInTheDocument();
	});

	it("shows the API's message when the rules are refused", async () => {
		const { path } = screenedReview();
		const { user, form } = await openRules(path);
		const count = within(form).getByRole("spinbutton");

		await user.clear(count);
		await user.type(count, "0");
		await user.click(within(form).getByRole("button", { name: "Preview" }));

		expect(
			await within(form).findByText(
				"possibly_min_met must be a whole number of at least 1.",
			),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path } = screenedReview();
		const { user, form } = await openRules(path);
		await user.click(within(form).getByRole("button", { name: "Preview" }));
		await within(form).findByRole("button", { name: "Apply" });

		await expectNoAccessibilityViolations(form);
	});
});
