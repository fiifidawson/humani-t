/**
 * Decision briefs for the fake API: a brief, its evidence and its trace, in the
 * API's shapes. The handlers and access rules are in fakeReviews.ts.
 *
 * The example has three options, a disagreement, gaps, and one citation the
 * checks flagged (P9, no passage).
 */

import type { components } from "../api/schema.gen";

type Schemas = components["schemas"];
export type Brief = Schemas["BriefOut"];
export type Evidence = Schemas["EvidenceOut"];
export type Trace = Schemas["TraceOut"];
type Claim = Schemas["ClaimOut"];

export const CAVEAT =
	"This brief is an aid to a decision, not the decision. It covers only the papers and documents in this run; check the cited sources before acting.";

const evidence = (text: string, ...tags: string[]): Claim => ({
	text,
	support: "evidence",
	citations: tags.map((tag) => ({ tag, source_kind: "paper" })),
});

const reasoning = (text: string): Claim => ({
	text,
	support: "hypothesis",
	citations: [],
});

export const PAPER_IDS: Readonly<Record<string, string>> = {
	P1: "paper-0000-4000-8000-000000000101",
	P2: "paper-0000-4000-8000-000000000102",
	P3: "paper-0000-4000-8000-000000000103",
	P9: "paper-0000-4000-8000-000000000109",
};

export function exampleBrief(
	id: string,
	reviewId: string,
	runId: string,
	extra: Partial<Brief> = {},
): Brief {
	return {
		id,
		review_id: reviewId,
		run_id: runId,
		version: 1,
		created_at: "2026-11-03T10:58:00Z",
		question: "Should we switch to cash in the camps near [LOCATION_1]?",
		caveat: CAVEAT,
		bottom_line: [
			evidence(
				"Cash transfers are likely to improve food security where markets work.",
				"P1",
				"P2",
			),
			reasoning("Evidence for camps with weak markets is thin."),
		],
		options: [
			{
				name: "Cash transfers",
				description: "Unconditional cash, by mobile money where possible.",
				strength: "moderate",
				benefits: [
					evidence("Households buy what they most need", "P1", "P2"),
					reasoning("Supports local traders"),
				],
				risks: [evidence("Prices can rise in small markets", "P1")],
			},
			{
				name: "Vouchers",
				description: "Paper or electronic vouchers for named shops.",
				strength: "weak",
				benefits: [evidence("Limits price risk", "P2")],
				risks: [evidence("Cost more to run", "P9")],
			},
			{
				name: "In-kind food",
				description: "A fixed food basket.",
				strength: "insufficient",
				benefits: [reasoning("Recipients carry no cash")],
				risks: [],
			},
		],
		trade_offs: [
			{
				criterion: "Cost to deliver",
				is_humanitarian: false,
				assessments: [
					{ option: "Cash transfers", claim: evidence("Lowest", "P2") },
					{ option: "Vouchers", claim: reasoning("Middle") },
					{ option: "In-kind food", claim: evidence("Highest", "P2") },
				],
			},
			{
				criterion: "Do no harm",
				is_humanitarian: true,
				assessments: [
					{
						option: "Cash transfers",
						claim: evidence("Protection risks if cash is visible", "P3"),
					},
					{ option: "Vouchers", claim: evidence("Lower theft risk", "P3") },
					{ option: "In-kind food", claim: reasoning("Lowest risk") },
				],
			},
		],
		conflicts: [
			{
				question: "Do cash transfers raise local food prices?",
				conflict_type: "context",
				positions: [
					evidence("Yes, 5–12% in small markets", "P1"),
					evidence("No measurable effect in larger towns", "P2"),
				],
				meaning: reasoning("The effect likely depends on market size."),
			},
		],
		considerations: [
			{
				criterion: "Do no harm",
				option: "In-kind food",
				because: evidence("recipients carry no cash", "P3"),
			},
		],
		gaps: [
			"No studies from camps with fewer than 5 traders",
			"Little on effects after 12 months",
		],
		sources: [
			source(
				"P1",
				"Cash transfers and food prices in Mali",
				2023,
				"include",
				4,
			),
			source("P2", "Vouchers in Niger", 2021, "include", 5),
			source("P3", "Protection and cash in camps", 2022, "include", 3),
			{
				...source("P9", "A paper never retrieved", 2020, null, 1),
				has_passage: false,
			},
		],
		quality: {
			coverage: 2,
			groundedness: 2,
			disagreements: 1,
			uncertainty: 2,
			humanitarian_awareness: 2,
			specificity: 1,
		},
		warnings: [],
		model_id: "qwen3.5:9b",
		rounds: 3,
		kept_round: 2,
		max_rounds: 3,
		stopped_because: "accepted",
		...extra,
	};
}

function source(
	tag: string,
	title: string,
	year: number,
	label: string | null,
	citations: number,
): Brief["sources"][number] {
	return {
		tag,
		source_kind: "paper",
		paper_id: PAPER_IDS[tag] ?? null,
		has_passage: true,
		title,
		year,
		doi: `10.1000/${tag.toLowerCase()}`,
		url: null,
		screening_label: label,
		citations,
	};
}

/** Evidence per tag; P9 has none, as when the checks flag a citation. */
export function exampleEvidence(): Map<string, Evidence> {
	return new Map([
		[
			"P1",
			{
				tag: "P1",
				source_kind: "paper",
				paper_id: PAPER_IDS["P1"] ?? "",
				source: {
					title: "Cash transfers and food prices in Mali",
					year: 2023,
					doi: "10.1000/p1",
					url: null,
					screening_label: "include",
					rationale: "Reports food prices after cash distributions in camps.",
				},
				passages: [
					{
						text: "In markets near [LOCATION_1] with fewer than ten traders, staple prices rose by 5 to 12 percent.",
						locator: "abstract",
						found_by: "cash transfers prices",
						round: 0,
					},
					{
						text: "Larger towns showed no measurable change.",
						locator: "abstract",
						found_by: "price effects of cash in small markets",
						round: 1,
					},
				],
				cited_for: [
					{
						path: "options[0].risks[0]",
						section: "risk",
						option: "Cash transfers",
						criterion: null,
						text: "Prices can rise in small markets",
						support: "evidence",
					},
					{
						path: "bottom_line[0]",
						section: "bottom_line",
						option: null,
						criterion: null,
						text: "Cash transfers are likely to improve food security where markets work.",
						support: "evidence",
					},
				],
			},
		],
		[
			"P2",
			{
				tag: "P2",
				source_kind: "paper",
				paper_id: PAPER_IDS["P2"] ?? "",
				source: null,
				passages: [
					{
						text: "Vouchers limited price rises.",
						locator: "abstract",
						found_by: "vouchers",
						round: 0,
					},
				],
				cited_for: [],
			},
		],
		[
			"P3",
			{
				tag: "P3",
				source_kind: "paper",
				paper_id: PAPER_IDS["P3"] ?? "",
				source: null,
				passages: [
					{
						text: "Visible cash raised protection risks.",
						locator: "abstract",
						found_by: "protection cash",
						round: 0,
					},
				],
				cited_for: [],
			},
		],
	]);
}

export function exampleTrace(briefId: string): Trace {
	return {
		brief_id: briefId,
		model_id: "qwen3.5:9b",
		rounds: 3,
		kept_round: 2,
		max_rounds: 3,
		stopped_because: "accepted",
		steps: [
			{
				round: 0,
				role: "planner",
				status: "ok",
				seconds: 41,
				tokens: { prompt: 900, completion: 300 },
				model_id: "qwen3.5:9b",
				prompt_version: "brief.planner@1",
				output: {
					decision: "Choose between cash, vouchers or in-kind food",
					options: ["Cash transfers", "Vouchers", "In-kind food"],
					criteria: ["Do no harm", "Cost to deliver"],
					sub_questions: ["Do prices rise?", "What does delivery cost?"],
				},
			},
			{
				round: 0,
				role: "retriever",
				status: "ok",
				seconds: 1,
				queries: ["cash transfers prices", "vouchers"],
				passages_found: 5,
				passages_new: 5,
				passages_total: 5,
			},
			{
				round: 1,
				role: "writer",
				status: "ok",
				seconds: 120,
				tokens: { prompt: 20000, completion: 3000 },
				output: {
					options: ["Cash transfers", "Vouchers", "In-kind food"],
					claims: 14,
					evidence_claims: 10,
					cited_tags: 3,
				},
			},
			{
				round: 1,
				role: "checks",
				status: "ok",
				seconds: 0,
				problems: [
					{
						code: "option_without_evidence",
						message: "In-kind food has no sourced benefit.",
						where: "options[2]",
					},
				],
			},
			{
				round: 1,
				role: "critic",
				status: "ok",
				seconds: 30,
				tokens: { prompt: 20000, completion: 2000 },
				output: {
					verdict: "revise",
					overruled_by_checks: false,
					scores: { coverage: 1, groundedness: 2 },
					issues: ["Price effects in small markets are thin."],
					queries: ["price effects of cash in small markets"],
				},
			},
			{
				round: 2,
				role: "writer",
				status: "error",
				seconds: 5,
				error: "The model server isn't answering.",
			},
			{
				round: 2,
				role: "critic",
				status: "ok",
				seconds: 28,
				tokens: { prompt: 20300, completion: 3600 },
				output: {
					verdict: "accept",
					overruled_by_checks: false,
					scores: { coverage: 2, groundedness: 2 },
					issues: [],
					queries: [],
				},
			},
		],
		totals: { calls: 9, prompt_tokens: 61200, completion_tokens: 8900 },
		is_masked: true,
		manifest: {
			model_ids: { writer: "qwen3.5:9b" },
			prompt_versions: { "answer.writer": "brief.writer@2" },
			retriever: "bm25",
			max_rounds: 3,
		},
	};
}
