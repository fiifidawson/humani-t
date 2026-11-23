import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "../../api/schema.gen";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { CAVEAT } from "../../test/fakeBriefs";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";
import { setScreenWidth } from "../../test/viewport";

type Brief = components["schemas"]["BriefOut"];

function briefed(
	options: {
		role?: "owner" | "reviewer" | "viewer";
		state?: "running" | "done";
		brief?: Partial<Brief> | null;
		isViewFirstOn?: boolean;
	} = {},
) {
	const api = fakeApi();
	const me = api.addUser({ hasTotp: true });
	api.signInAs(me);
	const project = api.addProject(
		"Cash vs vouchers, Gao",
		options.role ?? "owner",
	);
	project.is_view_first_on = options.isViewFirstOn ?? false;
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
		me,
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

	it("draws each option's strength as a small meter beside its word", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		// The word is what a screen reader reads; the meter only repeats it (§10.2).
		const cash = within(canvas).getByRole("article", {
			name: "Cash transfers",
		});
		const meter = cash.querySelector("[data-meter]");
		expect(meter).toHaveAttribute("data-value", "2");
		expect(meter).toHaveAttribute("data-max", "3");
		expect(meter).toHaveAttribute("aria-hidden", "true");
		expect(cash).toHaveTextContent("Moderate evidence");
		const food = within(canvas).getByRole("article", { name: "In-kind food" });
		expect(food.querySelector("[data-meter]")).toHaveAttribute(
			"data-value",
			"0",
		);
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

describe("trade-off priorities as a view preference (UI_UX_SPEC §6.9)", () => {
	const rowsOf = (canvas: HTMLElement) =>
		within(within(canvas).getByRole("table", { name: "Trade-offs" }))
			.getAllByRole("rowheader")
			.map((row) => row.textContent ?? "");

	it("lists the humanitarian criteria first in the picker, marked as humanitarian", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);
		const picker = within(canvas).getByRole("group", {
			name: "What matters most to you?",
		});

		const boxes = within(picker).getAllByRole("checkbox");
		expect(boxes).toHaveLength(2);
		expect(boxes[0]).toHaveAccessibleName("Do no harm");
		expect(boxes[1]).toHaveAccessibleName("Cost to deliver");
		expect(boxes[0]).toHaveAccessibleDescription("humanitarian");
		expect(boxes[1]).not.toHaveAccessibleDescription("humanitarian");
	});

	it("keeps the picks in the address, so reopening the link shows the same priorities", async () => {
		const { path } = briefed();
		const { canvas, user, router, unmount } = await openBrief(path);

		await user.click(
			within(canvas).getByRole("checkbox", { name: /Cost to deliver/ }),
		);
		const href = router.state.location.href;
		unmount();
		renderApp(href);
		await screen.findByRole("navigation", { name: "Sections of this brief" });

		const again = screen.getByRole("region", { name: "Decision brief" });
		expect(
			within(again).getByRole("checkbox", { name: /Cost to deliver/ }),
		).toBeChecked();
		expect(rowsOf(again)[0]).toContain("Cost to deliver");
		expect(rowsOf(again)[0]).toContain("priority");
		// Positions, not names: the criteria are project text and stay out of the address.
		expect(href).not.toContain("Cost");
	});

	it("can keep the original order, still marking the picked rows as priorities", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);
		await user.click(
			within(canvas).getByRole("checkbox", { name: /Cost to deliver/ }),
		);
		expect(rowsOf(canvas)[0]).toContain("Cost to deliver");

		await user.click(
			within(canvas).getByRole("radio", { name: "Original order" }),
		);

		const rows = rowsOf(canvas);
		expect(rows[0]).toContain("Do no harm");
		expect(rows[1]).toContain("Cost to deliver");
		expect(rows[1]).toContain("priority");

		await user.click(
			within(canvas).getByRole("radio", { name: "Your priorities first" }),
		);
		expect(rowsOf(canvas)[0]).toContain("Cost to deliver");
	});

	it("shows one card per criterion on a narrow screen, with the options inside", async () => {
		setScreenWidth(400);
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);
		const tradeOffs = within(canvas).getByRole("region", {
			name: "Trade-offs",
		});

		expect(within(tradeOffs).queryByRole("table")).not.toBeInTheDocument();
		await user.click(
			within(tradeOffs).getByRole("checkbox", { name: /Cost to deliver/ }),
		);
		const cards = within(tradeOffs).getAllByRole("article");
		expect(
			cards.map(
				(card) => within(card).getByRole("heading", { level: 4 }).textContent,
			),
		).toEqual(["Cost to deliver", "Do no harm"]);
		expect(cards[0]).toHaveTextContent("priority");
		const options = within(cards[0] as HTMLElement).getAllByRole("term");
		expect(options.map((o) => o.textContent)).toEqual([
			"Cash transfers",
			"Vouchers",
			"In-kind food",
		]);
	});

	it("has no accessibility violations on a narrow screen with a priority picked", async () => {
		setScreenWidth(400);
		const { path } = briefed();
		const { canvas, user, container } = await openBrief(path);
		await user.click(
			within(canvas).getByRole("checkbox", { name: /Do no harm/ }),
		);

		await expectNoAccessibilityViolations(container);
	});
});

describe("your view first (UI_UX_SPEC §6.9)", () => {
	async function openAsked(path: string) {
		const view = renderApp(`${path}/brief`);
		const prompt = await screen.findByRole("region", {
			name: "Before you read the brief",
		});
		return { ...view, prompt };
	}

	it("asks which option you lean towards before showing the brief, when the project asks", async () => {
		const { path } = briefed({ isViewFirstOn: true });
		const { prompt } = await openAsked(path);

		const answers = within(
			within(prompt).getByRole("group", {
				name: "Which option do you lean towards now?",
			}),
		)
			.getAllByRole("button")
			.map((b) => b.textContent);
		expect(answers).toEqual([
			"Cash transfers",
			"Vouchers",
			"In-kind food",
			"Not sure",
		]);
		expect(within(prompt).getByRole("button", { name: "Skip" })).toBeVisible();
		expect(
			screen.queryByRole("table", { name: "Trade-offs" }),
		).not.toBeInTheDocument();
	});

	it("saves the answer for this brief and shows it in the header", async () => {
		const { api, path, run } = briefed({ isViewFirstOn: true });
		const { prompt, user } = await openAsked(path);

		await user.click(
			within(prompt).getByRole("button", { name: "Cash transfers" }),
		);

		expect(
			await screen.findByText(
				"You leaned towards Cash transfers before reading.",
			),
		).toBeVisible();
		expect(screen.getByRole("table", { name: "Trade-offs" })).toBeVisible();
		const sent = api.requests.filter(
			(r) => r.method === "PUT" && r.path.endsWith(`/runs/${run.id}/leaning`),
		);
		expect(sent.map((r) => r.body)).toEqual([{ option: "Cash transfers" }]);
	});

	it("saves 'Not sure' as no option", async () => {
		const { api, path, run } = briefed({ isViewFirstOn: true });
		const { prompt, user } = await openAsked(path);

		await user.click(within(prompt).getByRole("button", { name: "Not sure" }));

		expect(
			await screen.findByText("You were not sure before reading."),
		).toBeVisible();
		expect(api.requestsTo("PUT", `/runs/${run.id}/leaning`)[0]?.body).toEqual({
			option: null,
		});
	});

	it("can be skipped, saves nothing, and asks again next time", async () => {
		const { api, path, run } = briefed({ isViewFirstOn: true });
		const { prompt, user, unmount } = await openAsked(path);

		await user.click(within(prompt).getByRole("button", { name: "Skip" }));

		expect(
			await screen.findByRole("table", { name: "Trade-offs" }),
		).toBeVisible();
		expect(api.requestsTo("PUT", `/runs/${run.id}/leaning`)).toEqual([]);
		unmount();
		await openAsked(path);
	});

	it("does not ask again once you have answered", async () => {
		const { api, me, path, run } = briefed({ isViewFirstOn: true });
		api.leanings.add(run.id, me.id, "In-kind food");
		const { canvas } = await openBrief(path);

		expect(
			await within(canvas).findByText(
				"You leaned towards In-kind food before reading.",
			),
		).toBeVisible();
		expect(
			screen.queryByRole("region", { name: "Before you read the brief" }),
		).not.toBeInTheDocument();
	});

	it("does not ask when the project has it off, as it is by default", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		expect(canvas).not.toHaveTextContent("lean");
	});

	it("has no accessibility violations", async () => {
		const { path } = briefed({ isViewFirstOn: true });
		const { container } = await openAsked(path);

		await expectNoAccessibilityViolations(container);
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

		expect(canvas).toHaveTextContent("They are not a verification.");
		expect(canvas).not.toHaveTextContent("10 of 12");
	});

	it("draws each quality check as a labelled bar out of 2, with its word", async () => {
		const { path } = briefed();
		const { canvas, user } = await openBrief(path);

		await user.click(
			within(canvas).getByRole("button", { name: "Show quality checks" }),
		);

		const checks = within(canvas).getByRole("list", { name: "Quality checks" });
		const rows = within(checks).getAllByRole("listitem");
		expect(rows.map((row) => row.textContent)).toEqual([
			"Coverage: good",
			"Groundedness: good",
			"Disagreements: partly",
			"Uncertainty: good",
			"Humanitarian awareness: good",
			"Specificity: partly",
		]);
		expect(
			rows.map((row) =>
				row.querySelector("[data-meter]")?.getAttribute("data-value"),
			),
		).toEqual(["2", "2", "1", "2", "2", "1"]);
		expect(rows[0]?.querySelector("[data-meter]")).toHaveAttribute(
			"data-max",
			"2",
		);
	});

	it("lists the humanitarian principles the trade-offs weigh, and those they do not", async () => {
		const { path } = briefed();
		const { canvas } = await openBrief(path);

		const principles = within(canvas).getByRole("region", {
			name: "Humanitarian principles in this brief",
		});
		const items = within(principles).getAllByRole("listitem");
		expect(items.map((item) => item.textContent)).toEqual([
			"HumanityNot discussed",
			"NeutralityNot discussed",
			"IndependenceNot discussed",
			"ImpartialityNot discussed",
			expect.stringContaining("Do no harm"),
		]);
		const weighed = within(items[4] as HTMLElement).getByRole("link", {
			name: "Do no harm: weighed in the trade-offs",
		});
		const row = canvas.querySelector(weighed.getAttribute("href") ?? "#none");
		expect(row).toHaveTextContent("Do no harm");
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

	// The largest page axe checks: about 30 s on a busy laptop, so it gets a minute.
	it("has no accessibility violations, brief and panel together", async () => {
		const { path } = briefed();
		const { container } = renderApp(`${path}/brief?source=P1`);
		const panel = await screen.findByRole("complementary", { name: "P1" });
		await within(panel).findByText(/Passage 1 of 2/);

		await expectNoAccessibilityViolations(container);
	}, 60_000);
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
