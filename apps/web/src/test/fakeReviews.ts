/**
 * The review half of the fake API: reviews, plans, runs, gates and papers.
 *
 * It applies the rules the screens depend on: 404 for non-members, 403 for viewer
 * writes, plan steps in order or 409, and the first accept or gate decision wins.
 * The "model" masks known names, so receipts carry real placeholders. Run events
 * are not served here; tests drive them through FakeEventSource.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import {
	type Brief,
	type Evidence,
	exampleBrief,
	exampleEvidence,
	exampleTrace,
	type Trace,
} from "./fakeBriefs";
import { jsonBody, nextId, problem, text } from "./fakeHttp";
import { FakeQuestions } from "./fakeQuestions";

const API = "*/api/v1";

type Schemas = components["schemas"];
type Role = Schemas["Role"];
type Review = Schemas["ReviewOut"];
type Version = Schemas["VersionOut"];
// By position, not by name: the component's generated name changes when
// another schema of the same name appears in the API.
type Question = Schemas["PlanOut"]["questions"][number];
type Receipt = Schemas["ReceiptOut"];
type SentText = Schemas["SentTextOut"];
type Run = Schemas["RunOut"];
type Paper = Schemas["app__api__schemas__papers__PaperOut"];
type Author = Schemas["AuthorOut"];
type Tier = Schemas["ScreeningTier"];

export const MODEL_DOWN =
	"The model server isn't answering. Your question is saved. Try again in a minute.";
export const ALREADY_ACCEPTED =
	"Someone else already accepted this draft. Showing the latest.";

/** Names the fake "masker" recognises, as the API's detectors would. */
const SENSITIVE: readonly [string, Schemas["EntityType"]][] = [
	["Amadou Diallo", "PERSON"],
	["Gao", "LOCATION"],
	["Timbuktu", "LOCATION"],
];

const MODEL: Author = { kind: "model", user_id: null };
const MODEL_ID = "llama3.1:8b";

interface FakePlan {
	mode: Schemas["PlanMode"];
	questions: Question[];
	versions: Version[];
	receipts: Receipt[];
	pendingChange: Schemas["ChangeRequestOut"] | null;
}

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	roleIn: (projectId: string) => Role | null;
	userId: () => string | null;
}

type Concept = [term: string, synonyms: string[], addedBy?: Author];
export type Draft = [theme: string, concepts: Concept[]][];

export const FIRST_DRAFT: Draft = [
	[
		"intervention",
		[
			["cash transfers", ["cash assistance"]],
			["vouchers", ["food vouchers"]],
		],
	],
	["outcome", [["food security", ["hunger", "nutrition"]]]],
	["setting", [["displacement", ["internally displaced", "refugees"]]]],
];

const CRITERIA: Schemas["CriterionOut"][] = [
	{ id: "I1", kind: "inclusion", text: "Displaced households or camps" },
	{ id: "I2", kind: "inclusion", text: "Reports a food security outcome" },
	{ id: "E1", kind: "exclusion", text: "School feeding programmes" },
];

function mask(raw: string): { sent: string; counts: Record<string, number> } {
	const counts: Record<string, number> = {};
	let sent = raw;
	for (const [name, type] of SENSITIVE) {
		if (sent.includes(name)) {
			counts[type] = (counts[type] ?? 0) + 1;
			sent = sent.replaceAll(name, `[${type}_${counts[type]}]`);
		}
	}
	return { sent, counts };
}

function sentText(
	kind: SentText["kind"],
	raw: string,
	ref: string | null = null,
): SentText {
	return { kind, ref, ...mask(raw) };
}

function compile(themes: Schemas["ThemeOut"][]): string {
	return themes
		.map(
			(theme) =>
				`(${theme.concepts
					.flatMap((c) => [c.term, ...c.synonyms])
					.map((t) => `"${t}"`)
					.join(" OR ")})`,
		)
		.join(" AND ");
}

/** As the API works it out: the current version against the one accepted before it. */
function rerunFrom(versions: readonly Version[]): "queries" | "screen" | null {
	const current = versions.at(-1);
	if (current === undefined) {
		return null;
	}
	const before = versions
		.filter((v) => v.accepted_at !== null && v.version < current.version)
		.at(-1);
	if (before === undefined) {
		return "queries";
	}
	const terms = (v: Version) =>
		JSON.stringify(
			v.themes.map((t) => [t.name, t.concepts.map((c) => c.terms)]),
		);
	const criteria = (v: Version) => JSON.stringify(v.criteria);
	if (terms(before) !== terms(current)) {
		return "queries";
	}
	return criteria(before) !== criteria(current) ? "screen" : null;
}

export function draftVersion(
	number: number,
	draft: Draft,
	extra: Partial<Version> = {},
): Version {
	const themes: Schemas["ThemeOut"][] = draft.map(([name, concepts]) => ({
		name,
		concepts: concepts.map(([term, synonyms, addedBy]) => ({
			term,
			synonyms,
			terms: [term, ...synonyms].map((t, n) => ({
				text: t,
				added_by: n === synonyms.length && addedBy ? addedBy : MODEL,
			})),
		})),
	}));
	const isStrict = extra.is_strict ?? false;
	return {
		version: number,
		origin: isStrict ? "user_supplied" : "llm_assisted",
		is_strict: isStrict,
		author: MODEL,
		created_at: `2026-11-03T10:4${number}:00Z`,
		accepted_at: null,
		accepted_by: null,
		model_id: isStrict ? null : MODEL_ID,
		prompt_version: isStrict ? null : "plan.draft@1",
		themes,
		criteria: CRITERIA,
		notes: ['"prices" and "price changes" merged'],
		change_request: null,
		clarifications: [],
		query: {
			rendered: compile(themes),
			themes: themes.map((t) => ({
				theme: t.name,
				groups: t.concepts.map((c) => ({
					concept: c.term,
					terms: [c.term, ...c.synonyms],
				})),
			})),
			term_count: themes.reduce(
				(n, t) => n + t.concepts.reduce((m, c) => m + 1 + c.synonyms.length, 0),
				0,
			),
			provenance: {
				vocabulary_version: number,
				vocabulary_origin: isStrict ? "user_supplied" : "llm_assisted",
				is_strict: isStrict,
			},
		},
		...extra,
	};
}

const QUESTIONS: Question[] = [
	{
		id: "q1",
		text: "Which outcomes matter most?",
		suggestions: ["Food security", "Market prices", "Protection risks"],
		answer: null,
		skipped: false,
	},
	{
		id: "q2",
		text: "Which places should the search cover?",
		suggestions: ["Sahel", "West Africa"],
		answer: null,
		skipped: false,
	},
];

const TIER_ORDER: readonly Tier[] = [
	"highly_relevant",
	"relevant",
	"possibly_relevant",
	"uncertain",
	"excluded",
];

const TIER_DECISION: Record<
	Tier,
	Pick<Schemas["DecisionOut"], "label" | "priority">
> = {
	highly_relevant: { label: "include", priority: "high" },
	relevant: { label: "include", priority: "medium" },
	possibly_relevant: { label: "include", priority: "low" },
	excluded: { label: "exclude", priority: "low" },
	uncertain: { label: "uncertain", priority: "low" },
};

export interface PaperOptions {
	title: string;
	tier?: Tier | null;
	year?: number;
	rationale?: string;
	maskedText?: string | null;
}

export class FakeReviews {
	readonly all: Review[] = [];
	readonly plans = new Map<string, FakePlan>();
	readonly runs: Run[] = [];
	readonly gates = new Map<
		string,
		{
			runId: string;
			kind: Schemas["GateRequiredData"]["kind"];
			decided: boolean;
		}
	>();
	readonly papers = new Map<
		string,
		{ paper: Paper; maskedText: string | null }[]
	>();
	readonly briefs: {
		brief: Brief;
		evidence: Map<string, Evidence>;
		trace: Trace;
	}[] = [];
	/** When true, every planning call fails as an unreachable model server does (502). */
	isModelDown = false;
	/** What the next change request alters: the search terms, or only the criteria. */
	nextChange: "search" | "criteria" = "search";
	/** When set, planning calls wait for it, so a test can see what shows meanwhile. */
	modelReply: Promise<void> | null = null;
	/** Follow-up questions and their threads (fakeQuestions.ts). */
	readonly questions = new FakeQuestions({
		reach: (reviewId, write) => this.reach(reviewId, write),
		userId: () => this.access.userId(),
		mask,
		latestRunId: (reviewId) =>
			this.runs.find((r) => r.review_id === reviewId)?.id ?? null,
	});

	constructor(private readonly access: Access) {}

	add(projectId: string, question: string): Review {
		const review: Review = {
			id: nextId("r"),
			project_id: projectId,
			question,
			status: "new",
			created_at: "2026-11-03T10:40:00Z",
			// The fake keeps no names; screens that show the author set their own.
			created_by: { id: this.access.userId() ?? "", display_name: "Amara N." },
		};
		this.all.unshift(review);
		return review;
	}

	/** Put a review straight into planning, as POST …/plan would. */
	startPlan(review: Review, mode: Schemas["PlanMode"]): FakePlan {
		const plan: FakePlan = {
			mode,
			questions: mode === "deep" ? structuredClone(QUESTIONS) : [],
			versions: [],
			receipts: [],
			pendingChange: null,
		};
		this.plans.set(review.id, plan);
		const asked = sentText("question", review.question);
		if (mode === "deep") {
			plan.receipts.push(this.receipt("clarify", [asked]));
			review.status = "clarifying";
		} else {
			this.addDraft(review, plan, FIRST_DRAFT, [asked]);
		}
		return plan;
	}

	addDraft(
		review: Review,
		plan: FakePlan,
		draft: Draft,
		sent: SentText[],
		extra: Partial<Version> = {},
	): Version {
		const version = draftVersion(plan.versions.length + 1, draft, extra);
		plan.versions.push(version);
		plan.receipts.push(
			this.receipt(plan.versions.length === 1 ? "draft" : "revise", sent),
		);
		review.status = "drafting";
		return version;
	}

	/** The model call behind every receipt so far failed after the text was sent. */
	failDelivery(review: Review): void {
		for (const receipt of this.plans.get(review.id)?.receipts ?? []) {
			receipt.delivered = false;
		}
	}

	accept(review: Review): void {
		const current = this.plans.get(review.id)?.versions.at(-1);
		if (current !== undefined) {
			current.accepted_at = "2026-11-03T10:49:00Z";
			current.accepted_by = this.access.userId();
			review.status = "accepted";
		}
	}

	addRun(
		review: Review,
		state: Schemas["RunState"] = "running",
		stages: string[] = [
			"queries",
			"discover",
			"ingest",
			"mask",
			"screen",
			"export",
			"index",
			"answer",
		],
	): Run {
		const isFinished = ["done", "failed", "cancelled"].includes(state);
		const run: Run = {
			id: nextId("run"),
			review_id: review.id,
			stages,
			state,
			outcome: isFinished ? state : null,
			stages_finished: 0,
			cancel_requested: false,
			created_at: "2026-11-03T10:51:00Z",
			started_at: "2026-11-03T10:51:00Z",
			finished_at: isFinished ? "2026-11-03T11:18:00Z" : null,
		};
		this.runs.unshift(run);
		review.status =
			state === "queued" ? "running" : (state as Review["status"]);
		return run;
	}

	addGate(run: Run, kind: Schemas["GateRequiredData"]["kind"]): string {
		const gateId = nextId("g");
		this.gates.set(gateId, { runId: run.id, kind, decided: false });
		return gateId;
	}

	addPaper(review: Review, run: Run, options: PaperOptions): Paper {
		const list = this.papers.get(review.id) ?? [];
		const tier = options.tier === undefined ? "relevant" : options.tier;
		const paper: Paper = {
			paper_id: nextId("paper"),
			tag: `P${list.length + 1}`,
			position: list.length + 1,
			title: options.title,
			year: options.year ?? 2023,
			doi: `10.1000/${list.length + 1}`,
			url: null,
			source_id: "openalex",
			found_by: ["openalex"],
			is_open_access: true,
			screening_status: tier === null ? "not_screened" : "screened",
			decision:
				tier === null
					? null
					: {
							...TIER_DECISION[tier],
							tier,
							score: 0.8,
							rationale:
								options.rationale ??
								"Reports food security for displaced households.",
							criteria_matched: tier === "excluded" ? [] : ["I1", "I2"],
							exclusion_reasons: tier === "excluded" ? ["E1", "I2"] : [],
							is_unreadable: false,
							model_id: MODEL_ID,
							prompt_version: "screen@1",
							criteria_version: 1,
							evidence_kind: "abstract",
							decided_at: "2026-11-03T11:00:00Z",
						},
		};
		list.push({ paper, maskedText: options.maskedText ?? null });
		this.papers.set(review.id, list);
		void run;
		return paper;
	}

	/** Store the example brief for `run`, as the answer stage would. */
	addBrief(review: Review, run: Run, extra: Partial<Brief> = {}): Brief {
		const id = nextId("brief");
		const brief = exampleBrief(id, review.id, run.id, {
			version:
				this.briefs.filter((b) => b.brief.review_id === review.id).length + 1,
			...extra,
		});
		this.briefs.push({
			brief,
			evidence: exampleEvidence(),
			trace: exampleTrace(id),
		});
		return brief;
	}

	/** A brief the caller may read, or the 404 a non-member and a missing id both get. */
	private reachBrief(briefId: unknown) {
		const entry = this.briefs.find((b) => b.brief.id === briefId);
		const reached = this.reach(entry?.brief.review_id, false);
		if (reached instanceof Response) {
			return reached;
		}
		return entry ?? problem(404, "Not Found");
	}

	private receipt(purpose: Receipt["purpose"], items: SentText[]): Receipt {
		return {
			id: nextId("rc"),
			purpose,
			created_at: "2026-11-03T10:41:00Z",
			created_by: this.access.userId(),
			model_id: MODEL_ID,
			prompt_version: `plan.${purpose}@1`,
			delivered: true,
			masked_total: items.reduce(
				(n, item) => n + Object.values(item.counts).reduce((a, b) => a + b, 0),
				0,
			),
			items,
		};
	}

	private planOut(review: Review): Schemas["PlanOut"] {
		const plan = this.plans.get(review.id);
		const versions = plan?.versions ?? [];
		const accepted = versions.filter((v) => v.accepted_at !== null).at(-1);
		return {
			review_id: review.id,
			status: review.status,
			mode: plan?.mode ?? null,
			questions: plan?.questions ?? [],
			pending_change: plan?.pendingChange ?? null,
			current: versions.at(-1) ?? null,
			accepted_version: accepted?.version ?? null,
			rerun_from: rerunFrom(versions),
			versions: versions.map((v) => ({
				version: v.version,
				origin: v.origin,
				author: v.author,
				created_at: v.created_at,
				accepted_at: v.accepted_at,
			})),
			receipts: plan?.receipts ?? [],
		};
	}

	/**
	 * Find the review and check the caller may do this with it: 401, then the
	 * same 404 for a non-member as for a missing id, then 403 for a viewer.
	 */
	private reach(
		reviewId: unknown,
		write: boolean,
	): { review: Review; role: Role } | Response {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		const review = this.all.find((r) => r.id === reviewId);
		const role =
			review === undefined ? null : this.access.roleIn(review.project_id);
		if (review === undefined || role === null) {
			return problem(404, "Not Found");
		}
		if (write && role === "viewer") {
			return problem(403, "Only owners and reviewers can do this.");
		}
		return { review, role };
	}

	private reachRun(runId: unknown, write: boolean) {
		const run = this.runs.find((r) => r.id === runId);
		const reached = this.reach(run?.review_id, write);
		return reached instanceof Response || run === undefined
			? reached instanceof Response
				? reached
				: problem(404, "Not Found")
			: { ...reached, run };
	}

	private async callModel(): Promise<Response | null> {
		if (this.modelReply !== null) {
			await this.modelReply;
		}
		return this.isModelDown ? problem(502, MODEL_DOWN) : null;
	}

	handlers() {
		return [
			http.post(
				`${API}/projects/:projectId/reviews`,
				async ({ params, request }) => {
					const refused = this.access.refuseUnlessSignedIn();
					if (refused !== null) {
						return refused;
					}
					const projectId = String(params["projectId"]);
					const role = this.access.roleIn(projectId);
					if (role === null) {
						return problem(404, "Not Found");
					}
					if (role === "viewer") {
						return problem(
							403,
							"Only owners and reviewers can start a review.",
						);
					}
					const question = text(await jsonBody(request), "question").trim();
					if (question === "" || question.length > 2000) {
						return problem(422, "The request body is not valid.", {
							errors: [
								{
									loc: ["body", "question"],
									msg: "The question must be 1 to 2,000 characters.",
									type: "value_error",
								},
							],
						});
					}
					const review = this.add(projectId, question);
					return HttpResponse.json(review, {
						status: 201,
						headers: {
							location: `/api/v1/projects/${projectId}/reviews/${review.id}`,
						},
					});
				},
			),

			http.get(`${API}/projects/:projectId/reviews`, ({ params }) => {
				const refused = this.access.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const projectId = String(params["projectId"]);
				if (this.access.roleIn(projectId) === null) {
					return problem(404, "Not Found");
				}
				return HttpResponse.json(
					this.all.filter((r) => r.project_id === projectId),
				);
			}),

			http.get(`${API}/projects/:projectId/reviews/:reviewId`, ({ params }) => {
				const reached = this.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				return reached.review.project_id === params["projectId"]
					? HttpResponse.json(reached.review)
					: problem(404, "Not Found");
			}),

			http.get(`${API}/reviews/:reviewId/plan`, ({ params }) => {
				const reached = this.reach(params["reviewId"], false);
				return reached instanceof Response
					? reached
					: HttpResponse.json(this.planOut(reached.review));
			}),

			http.post(
				`${API}/reviews/:reviewId/plan`,
				async ({ params, request }) => {
					const reached = this.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					if (review.status !== "new" || this.plans.has(review.id)) {
						return problem(
							409,
							"This review already has a search plan. Showing it.",
						);
					}
					const mode = text(await jsonBody(request), "mode");
					if (mode !== "deep" && mode !== "shallow") {
						return problem(422, "The request body is not valid.");
					}
					const failed = await this.callModel();
					if (failed !== null) {
						return failed;
					}
					this.startPlan(review, mode);
					return HttpResponse.json(this.planOut(review), { status: 201 });
				},
			),

			http.post(
				`${API}/reviews/:reviewId/plan/answers`,
				async ({ params, request }) => {
					const reached = this.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					const plan = this.plans.get(review.id);
					if (review.status !== "clarifying" || plan === undefined) {
						return problem(409, "This review is not waiting for answers.");
					}
					const body = await jsonBody(request);
					const answers = Array.isArray(body["answers"]) ? body["answers"] : [];
					for (const item of answers as Record<string, unknown>[]) {
						const question = plan.questions.find(
							(q) => q.id === item["question_id"],
						);
						const answer = item["answer"];
						if (
							question === undefined ||
							(typeof answer === "string" && answer.trim() === "")
						) {
							return problem(422, "Answer the question, or skip it.");
						}
						question.answer = typeof answer === "string" ? answer : null;
						question.skipped = typeof answer !== "string";
					}
					if (body["draft_now"] === true) {
						for (const question of plan.questions) {
							if (question.answer === null) {
								question.skipped = true;
							}
						}
					}
					if (plan.questions.some((q) => q.answer === null && !q.skipped)) {
						return HttpResponse.json(this.planOut(review));
					}
					const failed = await this.callModel();
					if (failed !== null) {
						return failed;
					}
					const sent = [
						sentText("question", review.question),
						...plan.questions.flatMap((q) =>
							q.answer === null ? [] : [sentText("answer", q.answer, q.id)],
						),
					];
					this.addDraft(review, plan, FIRST_DRAFT, sent, {
						clarifications: structuredClone(plan.questions),
					});
					return HttpResponse.json(this.planOut(review));
				},
			),

			http.post(
				`${API}/reviews/:reviewId/plan/changes`,
				async ({ params, request }) => {
					const reached = this.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					const plan = this.plans.get(review.id);
					const current = plan?.versions.at(-1);
					if (plan === undefined || current === undefined) {
						return problem(409, "There is no draft to change yet.");
					}
					const body = await jsonBody(request);
					if (review.status === "running" || review.status === "waiting") {
						return problem(
							409,
							"The plan cannot change while a run is going. Wait for it, or cancel it.",
						);
					}
					if (body["based_on_version"] !== current.version) {
						return problem(
							409,
							"The draft has changed since you looked. Showing the latest.",
						);
					}
					if (current.is_strict) {
						return problem(409, "Your own terms are used exactly as given.");
					}
					const failed = await this.callModel();
					if (failed !== null) {
						return failed;
					}
					const person: Author = {
						kind: "person",
						user_id: this.access.userId(),
					};
					const request_text = text(body, "request");
					const isCriteriaOnly = this.nextChange === "criteria";
					const next: Draft = isCriteriaOnly
						? FIRST_DRAFT
						: FIRST_DRAFT.map(([theme, concepts]) => [
								theme,
								concepts.map(
									([term, synonyms]): Concept =>
										term === "cash transfers"
											? [term, [...synonyms, "mobile money"], person]
											: [term, synonyms],
								),
							]);
					// A draft asked for after a run is dated after it, as it would be.
					const afterRun = this.runs.some((r) => r.review_id === review.id);
					const at = afterRun
						? `2026-11-03T11:0${plan.versions.length + 1}:00Z`
						: "2026-11-03T10:45:00Z";
					this.addDraft(
						review,
						plan,
						next,
						[
							sentText("question", review.question),
							sentText("change_request", request_text),
						],
						{
							change_request: {
								id: nextId("cr"),
								text: request_text,
								requested_by: this.access.userId() ?? "",
								requested_at: at,
								based_on_version: current.version,
							},
							...(afterRun ? { created_at: at } : {}),
							...(isCriteriaOnly
								? {
										criteria: [
											...CRITERIA,
											{
												id: "E2",
												kind: "exclusion",
												text: "Published before 2015",
											},
										],
									}
								: {}),
						},
					);
					return HttpResponse.json(this.planOut(review));
				},
			),

			http.post(
				`${API}/reviews/:reviewId/plan/accept`,
				async ({ params, request }) => {
					const reached = this.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					const current = this.plans.get(review.id)?.versions.at(-1);
					const version = (await jsonBody(request))["version"];
					if (current === undefined || current.accepted_at !== null) {
						return problem(409, ALREADY_ACCEPTED);
					}
					if (version !== current.version) {
						return problem(
							409,
							"The draft has changed since you looked. Showing the latest.",
						);
					}
					this.accept(review);
					return HttpResponse.json(this.planOut(review));
				},
			),

			http.get(
				`${API}/reviews/:reviewId/plan/versions/:version`,
				({ params }) => {
					const reached = this.reach(params["reviewId"], false);
					if (reached instanceof Response) {
						return reached;
					}
					const versions = this.plans.get(reached.review.id)?.versions ?? [];
					const number = Number(params["version"]);
					const plan = versions.find((v) => v.version === number);
					if (plan === undefined) {
						return problem(404, "That version of the plan doesn't exist.");
					}
					const before = versions.find((v) => v.version === number - 1);
					return HttpResponse.json({ plan, diff: diff(before, plan) });
				},
			),

			http.post(
				`${API}/reviews/:reviewId/runs`,
				async ({ params, request }) => {
					const reached = this.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					if (
						this.runs.some(
							(r) =>
								r.review_id === review.id &&
								["queued", "running", "waiting"].includes(r.state),
						)
					) {
						return problem(409, "A run is already going for this review.");
					}
					const stages = (await jsonBody(request))["stages"];
					if (!Array.isArray(stages) || stages.length === 0) {
						return problem(422, "Name at least one stage.");
					}
					const run = this.addRun(review, "queued", stages.map(String));
					return HttpResponse.json(
						{ run_id: run.id },
						{
							status: 202,
							headers: { location: `/api/v1/runs/${run.id}/events` },
						},
					);
				},
			),

			http.get(`${API}/reviews/:reviewId/runs`, ({ params }) => {
				const reached = this.reach(params["reviewId"], false);
				return reached instanceof Response
					? reached
					: HttpResponse.json(
							this.runs.filter((r) => r.review_id === reached.review.id),
						);
			}),

			http.post(`${API}/runs/:runId/cancel`, ({ params }) => {
				const reached = this.reachRun(params["runId"], true);
				if (reached instanceof Response) {
					return reached;
				}
				const { run } = reached;
				if (run.outcome !== null) {
					return problem(409, "This run has already finished.");
				}
				run.state = "cancelled";
				run.outcome = "cancelled";
				return HttpResponse.json(run, { status: 202 });
			}),

			http.post(`${API}/gates/:gateId`, async ({ params, request }) => {
				const gate = this.gates.get(String(params["gateId"]));
				const reached = this.reachRun(gate?.runId, true);
				if (reached instanceof Response || gate === undefined) {
					return reached instanceof Response
						? reached
						: problem(404, "Not Found");
				}
				if (gate.kind === "privacy_review" && reached.role !== "owner") {
					return problem(403, "Only owners can approve masking.");
				}
				if (gate.decided) {
					return problem(409, "Someone already decided this.");
				}
				const decision = text(await jsonBody(request), "decision");
				if (decision !== "approve" && decision !== "reject") {
					return problem(422, "The request body is not valid.");
				}
				gate.decided = true;
				return HttpResponse.json({
					gate_id: String(params["gateId"]),
					decision,
					by: this.access.userId() ?? "",
				});
			}),

			http.get(`${API}/reviews/:reviewId/papers`, ({ params, request }) => {
				const reached = this.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const list = this.papers.get(reached.review.id);
				if (list === undefined || list.length === 0) {
					return problem(404, "This review has no papers yet.");
				}
				const query = new URL(request.url).searchParams;
				const label = query.get("label");
				const page = Number(query.get("page") ?? "1");
				if (
					label !== null &&
					!["include", "exclude", "uncertain", "unscreened"].includes(label)
				) {
					return problem(
						422,
						"label must be include, exclude, uncertain or unscreened.",
					);
				}
				const matching = list
					.map((entry) => entry.paper)
					.filter((p) =>
						label === null
							? true
							: label === "unscreened"
								? p.decision === null
								: p.decision?.label === label,
					)
					.sort((a, b) => rank(a) - rank(b) || a.position - b.position);
				const counts: Record<string, number> = { unscreened: 0 };
				for (const { paper } of list) {
					const tier = paper.decision?.tier ?? "unscreened";
					counts[tier] = (counts[tier] ?? 0) + 1;
				}
				return HttpResponse.json({
					run_id:
						this.runs.find((r) => r.review_id === reached.review.id)?.id ?? "",
					page,
					page_size: 50,
					total: matching.length,
					counts,
					papers: matching.slice((page - 1) * 50, page * 50),
				});
			}),

			http.get(`${API}/reviews/:reviewId/papers/:paperId`, ({ params }) => {
				const reached = this.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const entry = this.papers
					.get(reached.review.id)
					?.find((e) => e.paper.paper_id === params["paperId"]);
				return entry === undefined
					? problem(404, "Not Found")
					: HttpResponse.json({
							run_id:
								this.runs.find((r) => r.review_id === reached.review.id)?.id ??
								"",
							paper: entry.paper,
							text_kind: entry.maskedText === null ? null : "abstract",
							masked_text: entry.maskedText,
						});
			}),

			http.get(`${API}/reviews/:reviewId/brief`, ({ params, request }) => {
				const reached = this.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const runId = new URL(request.url).searchParams.get("run_id");
				const found = this.briefs
					.map((b) => b.brief)
					.filter(
						(b) =>
							b.review_id === reached.review.id &&
							(runId === null || b.run_id === runId),
					)
					.sort((a, b) => b.version - a.version)[0];
				return found === undefined
					? problem(404, "This review has no decision brief yet.")
					: HttpResponse.json(found);
			}),

			http.get(`${API}/briefs/:briefId/evidence/:tag`, ({ params }) => {
				const entry = this.reachBrief(params["briefId"]);
				if (entry instanceof Response) {
					return entry;
				}
				const found = entry.evidence.get(String(params["tag"]));
				return found === undefined
					? problem(
							404,
							"This citation has no passage in the brief's evidence.",
						)
					: HttpResponse.json(found);
			}),

			http.get(`${API}/briefs/:briefId/trace`, ({ params }) => {
				const entry = this.reachBrief(params["briefId"]);
				return entry instanceof Response
					? entry
					: HttpResponse.json(entry.trace);
			}),

			http.get(`${API}/reviews/:reviewId/prisma`, ({ params }) => {
				const reached = this.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const papers = (this.papers.get(reached.review.id) ?? []).map(
					(e) => e.paper,
				);
				const count = (label: string | null) =>
					papers.filter((p) => (p.decision?.label ?? null) === label).length;
				return HttpResponse.json({
					run_id:
						this.runs.find((r) => r.review_id === reached.review.id)?.id ?? "",
					identified: papers.length + 3,
					identified_by_source: { openalex: papers.length + 3 },
					failed_sources: [],
					duplicates_removed: 3,
					after_deduplication: papers.length,
					screened: papers.length - count(null),
					included: count("include"),
					excluded: count("exclude"),
					uncertain: count("uncertain"),
					not_screened: count(null),
					excluded_by_reason: { E1: count("exclude") },
				});
			}),

			...this.questions.handlers(),
		];
	}
}

function rank(paper: Paper): number {
	const tier = paper.decision?.tier;
	return tier === undefined ? TIER_ORDER.length : TIER_ORDER.indexOf(tier);
}

function termsOf(version: Version | undefined): Map<string, Author> {
	const terms = new Map<string, Author>();
	for (const theme of version?.themes ?? []) {
		for (const concept of theme.concepts) {
			for (const term of concept.terms) {
				terms.set(
					`${theme.name}\u0000${concept.term}\u0000${term.text}`,
					term.added_by,
				);
			}
		}
	}
	return terms;
}

function diff(before: Version | undefined, after: Version): Schemas["DiffOut"] {
	const old = termsOf(before);
	const now = termsOf(after);
	const change = (key: string, kind: Schemas["ChangeKind"]) => {
		const [theme = "", concept = "", term = ""] = key.split("\u0000");
		return { theme, concept, term, change: kind, by: after.author };
	};
	return {
		from_version: before?.version ?? null,
		to_version: after.version,
		concepts: [],
		terms:
			before === undefined
				? []
				: [
						...[...now.keys()]
							.filter((k) => !old.has(k))
							.map((k) => change(k, "added")),
						...[...old.keys()]
							.filter((k) => !now.has(k))
							.map((k) => change(k, "removed")),
					],
		criteria: [],
	};
}
