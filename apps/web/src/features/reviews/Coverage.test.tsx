import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api/schema.gen";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

type Coverage = components["schemas"]["CoverageOut"];

const ESTIMATED: Coverage = {
	method: "chapman-pairs",
	method_version: 1,
	rules_version: null,
	relevant_found: 12,
	searched_sources: ["europepmc", "openalex"],
	overlaps: [
		{
			sources: ["europepmc", "openalex"],
			first_found: 10,
			second_found: 10,
			both_found: 8,
		},
	],
	interval: {
		sources: ["europepmc", "openalex"],
		estimated_total: 12,
		low_percent: 70,
		high_percent: 100,
	},
	no_estimate_because: null,
};

function none(reason: Coverage["no_estimate_because"]): Coverage {
	return {
		...ESTIMATED,
		searched_sources: ["openalex"],
		overlaps: [],
		interval: null,
		no_estimate_because: reason,
	};
}

function screenedReview(coverage: Coverage) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "owner");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	api.reviews.addPaper(review, run, {
		title: "Cash transfers and food prices in Mali",
		tier: "highly_relevant",
	});
	api.reviews.coverage.set(review.id, coverage);
	return { api, path: `/p/${project.id}/r/${review.id}/papers` };
}

async function coverageSection(coverage: Coverage) {
	const { path } = screenedReview(coverage);
	const rendered = renderApp(path);
	const canvas = await screen.findByRole("region", { name: "Papers · run 1" });
	const section = await within(canvas).findByRole("region", {
		name: "Coverage estimate",
	});
	return { ...rendered, section };
}

beforeEach(() => {
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

describe("the coverage estimate (UI_UX_SPEC §6.8)", () => {
	it("shows coverage as a range, always called an estimate", async () => {
		const { section } = await coverageSection(ESTIMATED);

		expect(section).toHaveTextContent(
			"Estimate: this run found between 70% and 100% of the relevant papers its searches could reach.",
		);
	});

	it("shows no single figure the data can't support", async () => {
		const { section } = await coverageSection(ESTIMATED);

		expect(section.textContent?.match(/[\d.]+%/g)).toEqual(["70%", "100%"]);
	});

	it("says which sources and counts it was worked out from", async () => {
		const { section, user } = await coverageSection(ESTIMATED);

		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		expect(section).toHaveTextContent(
			"Of the 12 relevant papers this run found, 8 were found by both Europe PMC and OpenAlex.",
		);
	});

	it("says in plain words what the estimate doesn't cover and why it may be too high", async () => {
		const { section, user } = await coverageSection(ESTIMATED);

		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		expect(section).toHaveTextContent("Not a count of everything published");
		expect(section).toHaveTextContent("the top of the range likely too high");
	});

	it("names its method and version", async () => {
		const { section, user } = await coverageSection(ESTIMATED);

		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		expect(section).toHaveTextContent(
			"Method: capture–recapture between two sources (Chapman), version 1.",
		);
	});

	it("says when it counted the labels as the model gave them", async () => {
		const { section, user } = await coverageSection(ESTIMATED);

		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		expect(section).toHaveTextContent(
			"It counts the labels as the model gave them.",
		);
	});

	it("says when it counted the labels as the label rules set them", async () => {
		const { section, user } = await coverageSection({
			...ESTIMATED,
			rules_version: 2,
		});

		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		expect(section).toHaveTextContent(
			"It counts the labels as the label rules (version 2) set them.",
		);
	});

	it.each([
		["one_source", "Only one source was searched"],
		[
			"too_few_overlaps",
			"Too few of the relevant papers were found by more than one source",
		],
		[
			"sources_nested",
			"Every relevant paper one source found, another found too",
		],
		["screening_unfinished", "Screening hasn't finished"],
	] as const)(
		"says plainly there is no estimate when %s, and why",
		async (reason, why) => {
			const { section } = await coverageSection(none(reason));

			expect(section).toHaveTextContent("No coverage estimate for this run.");
			expect(section).toHaveTextContent(why);
			expect(section).not.toHaveTextContent("%");
		},
	);

	it("has no accessibility violations", async () => {
		const { section, user, container } = await coverageSection(ESTIMATED);
		await user.click(
			within(section).getByRole("button", { name: "What this estimate means" }),
		);

		await expectNoAccessibilityViolations(container);
	});
});
