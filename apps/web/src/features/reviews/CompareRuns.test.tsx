import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function reviewWithRuns(count: number) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "viewer");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const runs = Array.from({ length: count }, () =>
		api.reviews.addRun(review, "done"),
	);
	return {
		api,
		runs,
		base: `/p/${project.id}/r/${review.id}`,
	};
}

beforeEach(() => {
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

describe("compare runs (UI_UX_SPEC §7.4)", () => {
	it("is reached from a run: pick two runs and compare", async () => {
		const { runs, base } = reviewWithRuns(3);
		const newest = runs[2]?.id ?? "";
		const { user, router } = renderApp(`${base}/run/${newest}`);

		const picker = await screen.findByRole("group", { name: "Compare runs" });
		const first = within(picker).getByRole("combobox", { name: "Run" });
		const second = within(picker).getByRole("combobox", { name: "with" });
		expect(first).toHaveValue(newest);
		expect(
			within(second)
				.getAllByRole("option")
				.map((o) => o.textContent),
		).toEqual(["Run 3", "Run 2", "Run 1"]);
		await user.selectOptions(second, "Run 1");
		await user.click(within(picker).getByRole("button", { name: "Compare" }));

		await waitFor(() => expect(currentPath(router)).toBe(`${base}/compare`));
		expect(router.state.location.search).toEqual({
			a: newest,
			b: runs[0]?.id,
		});
	});

	it("offers no comparison while the review has one run", async () => {
		const { runs, base } = reviewWithRuns(1);
		renderApp(`${base}/run/${runs[0]?.id ?? ""}`);

		await screen.findByRole("region", { name: "Run 1" });
		expect(
			screen.queryByRole("group", { name: "Compare runs" }),
		).not.toBeInTheDocument();
	});

	it("shows setup, discovery, screening and brief side by side, as the API computed them", async () => {
		const { api, runs, base } = reviewWithRuns(2);
		api.reviews.comparisons.runParts = {
			cannot_compare: null,
			setup: {
				vocabulary_versions: { before: [2], after: [3] },
				sources: { before: ["openalex"], after: ["openalex", "arxiv"] },
				model_ids: { before: ["qwen3.5:9b"], after: ["qwen3.5:9b"] },
				queries_added: [{ source: "arxiv", query: "all:cash" }],
				queries_removed: [],
				prompt_versions: [
					{ name: "screen", before: "screen-2", after: "screen-3" },
				],
				settings_changed: [
					{ name: "discover.limit_per_source", before: 25, after: 50 },
				],
			},
			discovery: {
				before: { identified: 4, after_deduplication: 4 },
				after: { identified: 6, after_deduplication: 5 },
				only_before: [],
				only_after: [
					{ paper_id: "p5", title: "Mobile money after displacement" },
				],
				in_both: 4,
			},
			screening: {
				before: { include: 2, exclude: 1, uncertain: 0, unscreened: 1 },
				after: { include: 1, exclude: 2, uncertain: 0, unscreened: 2 },
				changed: [
					{
						paper_id: "p2",
						title: "Food vouchers in displacement camps",
						before: "include",
						after: "exclude",
						reason: "Vouchers are not cash.",
					},
				],
			},
			brief: {
				options_added: ["Vouchers"],
				options_removed: [],
				strength_changes: [
					{ option: "Cash transfers", before: "weak", after: "moderate" },
				],
				sources_added: [],
				sources_removed: [],
			},
		};
		renderApp(`${base}/compare?a=${runs[1]?.id}&b=${runs[0]?.id}`);

		const canvas = await screen.findByRole("region", { name: "Compare runs" });
		const setup = await within(canvas).findByRole("region", { name: "Setup" });
		expect(
			within(setup).getByRole("row", { name: /Vocabulary version/ }),
		).toHaveTextContent("Vocabulary version23");
		expect(setup).toHaveTextContent("Queries added: arxiv: all:cash");
		expect(setup).toHaveTextContent("discover.limit_per_source: 25 → 50");
		const discovery = within(canvas).getByRole("region", { name: "Discovery" });
		expect(discovery).toHaveTextContent("Only in run 2 (1)");
		expect(discovery).toHaveTextContent("Mobile money after displacement");
		const screening = within(canvas).getByRole("region", { name: "Screening" });
		expect(screening).toHaveTextContent(
			"Food vouchers in displacement camps: Included → Excluded. Vouchers are not cash.",
		);
		const brief = within(canvas).getByRole("region", { name: "Brief" });
		expect(brief).toHaveTextContent("Options added: Vouchers");
		expect(brief).toHaveTextContent("Cash transfers: Weak → Moderate");
		await expectNoAccessibilityViolations(canvas);
	});

	it("says why when a run has no record of its setup", async () => {
		const { api, runs, base } = reviewWithRuns(2);
		api.reviews.comparisons.runParts = {
			cannot_compare: "Run 1 can't be compared: it has no record of its setup.",
			setup: null,
			discovery: null,
			screening: null,
			brief: null,
		};
		renderApp(`${base}/compare?a=${runs[0]?.id}&b=${runs[1]?.id}`);

		expect(
			await screen.findByText(
				"Run 1 can't be compared: it has no record of its setup.",
			),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("region", { name: "Setup" }),
		).not.toBeInTheDocument();
	});
});
