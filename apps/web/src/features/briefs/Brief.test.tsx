import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "../../api/schema.gen";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { CAVEAT } from "../../test/fakeBriefs";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

type Brief = components["schemas"]["BriefOut"];

function briefed(
	options: {
		role?: "owner" | "reviewer" | "viewer";
		state?: "running" | "done";
		brief?: Partial<Brief> | null;
	} = {},
) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject(
		"Cash vs vouchers, Gao",
		options.role ?? "owner",
	);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, options.state ?? "done");
	const brief =
		options.brief === null
			? null
			: api.reviews.addBrief(review, run, options.brief);
	return {
		api,
		project,
		review,
		run,
		brief,
		path: `/p/${project.id}/r/${review.id}`,
	};
}

async function stream(runId: string): Promise<FakeEventSource> {
	const source = await waitFor(() => {
		const found = FakeEventSource.instances.find((s) =>
			s.url.startsWith(`/api/v1/runs/${runId}/events`),
		);
		expect(found).toBeDefined();
		return found as FakeEventSource;
	});
	act(() => source.open());
	return source;
}

function send(source: FakeEventSource, ...events: [string, unknown][]) {
	act(() => {
		for (const [type, data] of events) {
			source.emit(type, source.nextId, data);
			source.nextId += 1;
		}
	});
}

const at = "2026-11-03T10:51:00Z";
const answerStarted: [string, unknown] = [
	"stage.started",
	{ stage: "answer", position: 8, total: 8, at },
];
const answerFinished = (briefId: string): [string, unknown] => [
	"stage.finished",
	{ stage: "answer", seconds: 300, summary: { brief_id: briefId, version: 1 } },
];

async function openBrief(path: string) {
	const view = renderApp(`${path}/brief`);
	await screen.findByRole("navigation", { name: "Sections of this brief" });
	const canvas = screen.getByRole("region", { name: "Decision brief" });
	return { ...view, canvas };
}

describe("the brief card in the chat (UI_UX_SPEC §5.3.8)", () => {
	it("is posted when the Brief stage finishes: bottom line, options with strength, counts and the caveat", async () => {
		const { path, run, brief } = briefed({ state: "running" });
		renderApp(path);
		const source = await stream(run.id);

		send(source, answerStarted, answerFinished(brief?.id ?? ""));

		const card = await screen.findByRole("article", {
			name: "Decision brief · round 2 of 3 · accepted",
		});
		expect(card).toHaveTextContent(
			"Cash transfers are likely to improve food security where markets work.",
		);
		const options = within(card)
			.getAllByRole("listitem")
			.map((li) => li.textContent);
		expect(options).toEqual([
			"Cash transfers · Moderate evidence",
			"Vouchers · Weak evidence",
			"In-kind food · Not enough evidence",
		]);
		expect(card).toHaveTextContent("1 disagreement · 2 gaps · 4 sources");
		expect(card).toHaveTextContent(
			"An aid to a decision, not the decision. Check the sources.",
		);
		expect(
			within(card).getByRole("link", { name: "Open brief" }),
		).toBeInTheDocument();
		expect(
			within(card).getByRole("link", { name: "How this was made" }),
		).toBeInTheDocument();
	});

	it("puts a warning where the counts were while checks still fail", async () => {
		const { path, run, brief } = briefed({
			state: "running",
			brief: {
				warnings: [
					{
						code: "option_without_evidence",
						message: "In-kind food has no sourced benefit.",
						where: "options[2]",
					},
				],
			},
		});
		renderApp(path);
		const source = await stream(run.id);

		send(source, answerStarted, answerFinished(brief?.id ?? ""));

		const card = await screen.findByRole("article", { name: /Decision brief/ });
		expect(card).toHaveTextContent("1 check still failing · see the brief");
		expect(card).not.toHaveTextContent("1 disagreement");
	});
});

describe("the brief canvas: header (UI_UX_SPEC §6.9, §8.6, §8.7)", () => {
	it("opens from the card and shows the question as sent, masked, with its meta line", async () => {
		const { path, project, review, run } = briefed();
		const { user, router } = renderApp(path);
		const source = await stream(run.id);
		send(source, answerStarted, answerFinished(""));
		const card = await screen.findByRole("article", { name: /Decision brief/ });

		await user.click(within(card).getByRole("link", { name: "Open brief" }));

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}/brief`),
		);
		await screen.findByRole("navigation", { name: "Sections of this brief" });
		const canvas = screen.getByRole("region", { name: "Decision brief" });
		expect(within(canvas).getByText("masked location 1")).toBeInTheDocument();
		expect(canvas).toHaveTextContent("Version 1");
		expect(canvas).toHaveTextContent("Made by qwen3.5:9b");
		expect(canvas).toHaveTextContent("kept round 2 of 3");
		expect(canvas).toHaveTextContent("accepted by the Critic");
	});

	it("keeps the full caveat in the header, always visible, with failing checks in the same box", async () => {
		const { path } = briefed({
			brief: {
				warnings: [
					{
						code: "option_without_evidence",
						message: "In-kind food has no sourced benefit.",
						where: "options[2]",
					},
				],
			},
		});
		const { canvas } = await openBrief(path);

		const note = within(canvas).getByRole("note");
		expect(note).toHaveTextContent(CAVEAT);
		expect(note).toHaveTextContent("In-kind food has no sourced benefit.");
		expect(within(note).queryByRole("button")).not.toBeInTheDocument();
	});

	it("says there is no brief yet when the Brief stage has not finished", async () => {
		const { path } = briefed({ brief: null });
		renderApp(`${path}/brief`);

		expect(
			await screen.findByText(
				"No decision brief yet. It appears when a run's Brief stage finishes.",
			),
		).toBeInTheDocument();
	});
});

describe("options, evidence and reasoning (UI_UX_SPEC §6.9, §8.1, §8.2)", () => {
	it("keeps the Writer's order, with no ranking, each with its strength as a word", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		const options = within(canvas).getByRole("region", { name: "Options" });
		const cards = within(options).getAllByRole("article");
		expect(
			cards.map(
				(card) => within(card).getByRole("heading", { level: 4 }).textContent,
			),
		).toEqual(["Cash transfers", "Vouchers", "In-kind food"]);
		expect(cards[0]).toHaveTextContent("Moderate evidence");
		expect(cards[2]).toHaveTextContent("Not enough evidence");
	});

	it("reads benefits and risks out, and marks reasoning as reasoning, without chips", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);
		const cash = within(canvas).getByRole("article", {
			name: "Cash transfers",
		});

		const benefits = within(cash).getByRole("list", { name: "Benefits" });
		const [sourced, reasoned] = within(benefits).getAllByRole("listitem");
		expect(sourced).toHaveTextContent(
			"Benefit: Households buy what they most need",
		);
		expect(
			within(sourced as HTMLElement).getAllByRole("button", {
				name: /^Source P/,
			}),
		).toHaveLength(2);
		expect(reasoned).toHaveTextContent("Supports local traders");
		expect(reasoned).toHaveTextContent("reasoning, not a source");
		expect(
			within(reasoned as HTMLElement).queryByRole("button"),
		).not.toBeInTheDocument();
		expect(
			within(within(cash).getByRole("list", { name: "Risks" })).getByRole(
				"listitem",
			),
		).toHaveTextContent("Risk: Prices can rise in small markets");
	});

	it("explains what each strength means", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);

		await user.click(
			within(canvas).getByRole("button", { name: "What the strengths mean" }),
		);

		expect(canvas).toHaveTextContent(
			"Weak evidence: Few or weaker studies. Treat as a lead, not a finding",
		);
	});
});

describe("trade-offs and priorities (UI_UX_SPEC §6.9)", () => {
	it("is a real table, criteria by options, humanitarian criteria first and marked", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		const table = within(canvas).getByRole("table", { name: "Trade-offs" });
		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((h) => h.textContent),
		).toEqual(["Criterion", "Cash transfers", "Vouchers", "In-kind food"]);
		const rows = within(table).getAllByRole("rowheader");
		expect(rows[0]).toHaveTextContent("Do no harm");
		expect(rows[0]).toHaveTextContent("humanitarian");
		expect(rows[1]).toHaveTextContent("Cost to deliver");
	});

	it("moves a picked priority to the top, says so, and shows what the brief says if it matters most", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);
		const picker = within(canvas).getByRole("group", {
			name: "What matters most to you?",
		});

		await user.click(
			within(picker).getByRole("checkbox", { name: "Cost to deliver" }),
		);
		let rows = within(
			within(canvas).getByRole("table", { name: "Trade-offs" }),
		).getAllByRole("rowheader");
		expect(rows[0]).toHaveTextContent("Cost to deliver");
		expect(rows[0]).toHaveTextContent("priority");

		await user.click(
			within(picker).getByRole("checkbox", { name: "Do no harm" }),
		);
		rows = within(
			within(canvas).getByRole("table", { name: "Trade-offs" }),
		).getAllByRole("rowheader");
		expect(rows.map((row) => row.textContent?.includes("priority"))).toEqual([
			true,
			true,
		]);
		expect(canvas).toHaveTextContent(
			"If Do no harm matters most: In-kind food, because recipients carry no cash",
		);
		expect(
			within(canvas).getByRole("article", { name: "In-kind food" }),
		).toHaveTextContent("Fits your priority");
	});

	it("can show the humanitarian criteria only", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);

		await user.click(
			within(canvas).getByRole("checkbox", {
				name: "Show humanitarian criteria only",
			}),
		);

		const rows = within(
			within(canvas).getByRole("table", { name: "Trade-offs" }),
		).getAllByRole("rowheader");
		expect(rows).toHaveLength(1);
		expect(rows[0]).toHaveTextContent("Do no harm");
	});

	it("selects a priority from 'If this matters most'", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);
		const section = within(canvas).getByRole("list", {
			name: "If this matters most",
		});

		await user.click(
			within(section).getByRole("button", { name: "Do no harm" }),
		);

		expect(
			within(section).getByRole("button", { name: "Do no harm" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			within(canvas).getByRole("checkbox", { name: "Do no harm" }),
		).toBeChecked();
	});
});

describe("disagreements, gaps, sources and checks (UI_UX_SPEC §6.9, §8.3)", () => {
	it("says why sources disagree in plain words, with each position and what it means", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		const conflict = within(canvas).getByRole("article", {
			name: "Do cash transfers raise local food prices?",
		});
		expect(conflict).toHaveTextContent("Different settings, different results");
		expect(conflict).toHaveTextContent("Ask which setting is closest to yours");
		const positions = within(conflict).getByRole("list", { name: "Positions" });
		expect(within(positions).getAllByRole("listitem")[0]).toHaveTextContent(
			"Position 1: Yes, 5–12% in small markets",
		);
		expect(conflict).toHaveTextContent(
			"What this means: The effect likely depends on market size.",
		);
	});

	it("lists gaps and every cited source by first appearance, with label and citation count", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		expect(
			within(canvas).getByRole("heading", { name: "Gaps in the evidence (2)" }),
		).toBeInTheDocument();
		const sources = within(
			within(canvas).getByRole("list", { name: "Sources" }),
		).getAllByRole("listitem");
		expect(sources[0]).toHaveTextContent(
			"Cash transfers and food prices in Mali",
		);
		expect(sources[0]).toHaveTextContent("Included");
		expect(sources[0]).toHaveTextContent("cited 4 times");
		expect(sources[3]).toHaveTextContent("source missing");
	});

	it("keeps the quality checks folded, and never adds them up", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);

		await user.click(
			within(canvas).getByRole("button", { name: "Show quality checks" }),
		);

		expect(canvas).toHaveTextContent("Coverage: good (2 of 2)");
		expect(canvas).toHaveTextContent("Disagreements: partly (1 of 2)");
		expect(canvas).toHaveTextContent("They are not a verification.");
		expect(canvas).not.toHaveTextContent("10 of 12");
	});
});

describe("the source panel (UI_UX_SPEC §6.10, §8.4)", () => {
	it("opens from a citation chip with the passages, what it was cited for, and why it was included", async () => {
		const { path } = briefed();
		const { canvas, user, router } = await openBrief(path);

		const [chip] = within(canvas).getAllByRole("button", {
			name: "Source P1, Cash transfers and food prices in Mali, 2023",
		});
		await user.click(chip as HTMLElement);

		const panel = await screen.findByRole("complementary", { name: "P1" });
		expect(router.state.location.search).toMatchObject({ source: "P1" });
		expect(panel).toHaveTextContent("Cash transfers and food prices in Mali");
		expect(panel).toHaveTextContent("Included");
		const citedFor = within(panel).getByRole("list", { name: "Cited for" });
		expect(within(citedFor).getAllByRole("listitem")[0]).toHaveTextContent(
			"Prices can rise in small markets",
		);
		expect(panel).toHaveTextContent(
			"Passage 1 of 2 · found by: cash transfers prices",
		);
		expect(within(panel).getByText("masked location 1")).toBeInTheDocument();

		await user.click(
			within(panel).getByRole("button", { name: "Next passage" }),
		);
		expect(panel).toHaveTextContent("Passage 2 of 2");

		await user.click(
			within(panel).getByRole("button", { name: "Why it was included" }),
		);
		expect(panel).toHaveTextContent(
			"Reports food prices after cash distributions in camps.",
		);
	});

	it("closes with Esc first, leaving the brief open", async () => {
		const { path } = briefed();
		const { user } = renderApp(`${path}/brief?source=P1`);
		await screen.findByRole("complementary", { name: "P1" });

		await user.keyboard("{Escape}");

		await waitFor(() =>
			expect(
				screen.queryByRole("complementary", { name: "P1" }),
			).not.toBeInTheDocument(),
		);
		expect(
			screen.getByRole("region", { name: "Decision brief" }),
		).toBeInTheDocument();
	});

	it("says a source is missing when the citation has no passage", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);

		await user.click(
			within(canvas).getAllByRole("button", {
				name: "Source P9, source missing",
			})[0] as HTMLElement,
		);

		const panel = await screen.findByRole("complementary", { name: "P9" });
		expect(
			await within(panel).findByText(
				"This citation has no passage. Report it.",
			),
		).toBeInTheDocument();
	});

	it("has no accessibility violations, brief and panel together", async () => {
		const { path } = briefed();
		const { container } = renderApp(`${path}/brief?source=P1`);
		const panel = await screen.findByRole("complementary", { name: "P1" });
		await within(panel).findByText(/Passage 1 of 2/);

		await expectNoAccessibilityViolations(container);
	});
});

describe("the brief for a viewer", () => {
	it("is readable by every member, viewers included", async () => {
		const { path } = briefed({ role: "viewer" });
		await openBrief(path);

		expect(
			screen.getByRole("table", { name: "Trade-offs" }),
		).toBeInTheDocument();
	});
});
