import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";
import { clockTime } from "../reviews/format";

const FIRST_AT = "2026-11-03T10:31:00Z";

function twoVersions() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "viewer");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const first = api.reviews.addRun(review, "done");
	api.reviews.addBrief(review, first, { created_at: FIRST_AT });
	const second = api.reviews.addRun(review, "done");
	api.reviews.addBrief(review, second);
	return { api, path: `/p/${project.id}/r/${review.id}/brief` };
}

async function openCanvas(path: string) {
	const view = renderApp(path);
	await screen.findByRole("navigation", { name: "Sections of this brief" });
	return {
		...view,
		canvas: screen.getByRole("region", { name: "Decision brief" }),
	};
}

describe("brief versions (UI_UX_SPEC §6.9 Versions)", () => {
	it("lists every version in the header's menu, the latest marked", async () => {
		const { path } = twoVersions();
		const { canvas } = await openCanvas(path);

		const menu = await within(canvas).findByRole("combobox", {
			name: "Version",
		});
		const options = within(menu)
			.getAllByRole("option")
			.map((o) => o.textContent);
		expect(options).toEqual([
			"Version 2 (latest)",
			`Version 1, ${clockTime(FIRST_AT)}`,
		]);
	});

	it("opens an older version when it is chosen", async () => {
		const { path } = twoVersions();
		const { canvas, user } = await openCanvas(path);
		const menu = await within(canvas).findByRole("combobox", {
			name: "Version",
		});

		await user.selectOptions(menu, `Version 1, ${clockTime(FIRST_AT)}`);

		await waitFor(() =>
			expect(
				screen.getByRole("region", { name: "Decision brief" }),
			).toHaveTextContent("Version 1 ·"),
		);
	});

	it("shows no menu while there is only one version", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		const project = api.addProject("Cash vs vouchers, Gao", "owner");
		const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
		api.reviews.startPlan(review, "shallow");
		api.reviews.accept(review);
		api.reviews.addBrief(review, api.reviews.addRun(review, "done"));
		const { canvas } = await openCanvas(
			`/p/${project.id}/r/${review.id}/brief`,
		);

		await screen.findByText(/Version 1 ·/);
		expect(
			within(canvas).queryByRole("combobox", { name: "Version" }),
		).not.toBeInTheDocument();
	});

	it("compares with an older version: options, strength changes and sources, as the API computed them", async () => {
		const { api, path } = twoVersions();
		api.reviews.comparisons.briefChanges = {
			options_added: [],
			options_removed: ["In-kind food"],
			strength_changes: [
				{ option: "Cash transfers", before: "weak", after: "moderate" },
			],
			sources_added: [
				{
					paper_id: "paper-4",
					source_kind: "paper",
					tag: "P4",
					title: "Mobile money after displacement",
				},
			],
			sources_removed: [],
		};
		const { canvas, user } = await openCanvas(path);

		await user.selectOptions(
			await within(canvas).findByRole("combobox", { name: "Compare with" }),
			"Version 1",
		);

		const changes = await within(canvas).findByRole("region", {
			name: "Changes since version 1",
		});
		expect(changes).toHaveTextContent("Options removed: In-kind food");
		expect(changes).toHaveTextContent("Cash transfers: Weak → Moderate");
		expect(changes).toHaveTextContent(
			"Sources added: P4 Mobile money after displacement",
		);
		expect(changes).not.toHaveTextContent("Options added");
		await expectNoAccessibilityViolations(canvas);
	});

	it("says so when two versions differ in none of these", async () => {
		const { path } = twoVersions();
		const { canvas, user } = await openCanvas(path);

		await user.selectOptions(
			await within(canvas).findByRole("combobox", { name: "Compare with" }),
			"Version 1",
		);

		expect(
			await within(canvas).findByText(
				"Same options, evidence strengths and sources.",
			),
		).toBeInTheDocument();
	});
});
