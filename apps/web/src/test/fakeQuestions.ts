/**
 * Follow-up questions for the fake API, served beside fakeReviews.ts.
 *
 * It applies the rules the screens depend on: 202 with the question masked and
 * pending, 403 for viewers, 409 while a question waits, 404 for another review's
 * thread. The "worker" answers on the next poll, when the test says so.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { PAPER_IDS } from "./fakeBriefs";
import { jsonBody, nextId, problem } from "./fakeHttp";

const API = "*/api/v1";

type Schemas = components["schemas"];
type Role = Schemas["Role"];
type Review = Schemas["ReviewOut"];
type Thread = Schemas["ThreadOut"];
// By position, not by name: the generated name carries the module path.
type Question = Thread["questions"][number];
type Claim = Schemas["ClaimOut"];
type AnswerSource = Schemas["AnswerSourceOut"];

export const NOTHING_FOUND = "Nothing in this project answers that.";
const MAX_QUESTION_LENGTH = 2000;
const MODEL_ID = "llama3.1:8b";

/** What the worker does with a question it picks up. */
export type FakeOutcome =
	| { kind: "answer"; claims: Claim[]; sources: AnswerSource[] }
	| { kind: "nothing" }
	| { kind: "failed"; error: string };

export const EXAMPLE_ANSWER: FakeOutcome = {
	kind: "answer",
	claims: [
		{
			text: "Staple prices rose by 5 to 12 percent in small markets after cash distributions.",
			support: "evidence",
			citations: [{ tag: "P1", source_kind: "paper" }],
		},
		{
			text: "Larger towns may absorb cash without price rises.",
			support: "hypothesis",
			citations: [],
		},
	],
	sources: [
		{
			tag: "P1",
			source_kind: "paper",
			paper_id: PAPER_IDS["P1"] ?? "",
			passages: [
				{
					text: "In markets near [LOCATION_1] with fewer than ten traders, staple prices rose by 5 to 12 percent.",
					locator: "abstract",
					found_by: "cash transfers prices",
					round: 0,
				},
			],
		},
	],
};

/** The example answer, citing the paper `paperId` as P1. */
export function answerCiting(paperId: string): FakeOutcome {
	if (EXAMPLE_ANSWER.kind !== "answer") {
		throw new Error("EXAMPLE_ANSWER must be an answer");
	}
	return {
		...EXAMPLE_ANSWER,
		sources: EXAMPLE_ANSWER.sources.map((source) => ({
			...source,
			paper_id: paperId,
		})),
	};
}

export const MODEL_DOWN_ANSWER: FakeOutcome = {
	kind: "failed",
	error: "The model server isn't answering. Ask again in a minute.",
};

interface Access {
	reach: (
		reviewId: unknown,
		write: boolean,
	) => { review: Review; role: Role } | Response;
	userId: () => string | null;
	mask: (raw: string) => { sent: string; counts: Record<string, number> };
	latestRunId: (reviewId: string) => string | null;
}

const isOpen = (question: Question) =>
	question.status === "pending" || question.status === "answering";

export class FakeQuestions {
	/** Newest first, as the API lists them. */
	readonly threads: Thread[] = [];
	/** What the worker does on the next poll of an open question; `null` keeps it working. */
	outcome: FakeOutcome | null = null;
	/** When set, asking waits for it, so a test can see what shows meanwhile. */
	askReply: Promise<void> | null = null;

	constructor(private readonly access: Access) {}

	/**
	 * A question already asked and answered, as if from an earlier visit: in a
	 * new thread, or continuing `threadId`.
	 */
	addAnswered(
		review: Review,
		question: string,
		outcome: FakeOutcome = EXAMPLE_ANSWER,
		askedAt = "2026-11-03T11:30:00Z",
		threadId: string | null = null,
	): Question {
		const thread =
			threadId === null ? null : this.threads.find((t) => t.id === threadId);
		if (thread === undefined) {
			throw new Error(`No thread ${threadId}`);
		}
		const asked = this.ask(review, question, thread, askedAt);
		this.finish(asked, outcome);
		return asked;
	}

	private ask(
		review: Review,
		raw: string,
		thread: Thread | null,
		askedAt = new Date().toISOString(),
	): Question {
		const { sent, counts } = this.access.mask(raw);
		const userId = this.access.userId() ?? "";
		const target = thread ?? {
			id: nextId("t"),
			review_id: review.id,
			created_at: askedAt,
			created_by: userId,
			questions: [],
		};
		if (thread === null) {
			this.threads.unshift(target);
		}
		const question: Question = {
			id: nextId("question"),
			thread_id: target.id,
			review_id: review.id,
			position: target.questions.length + 1,
			status: "pending",
			asked_by: userId,
			asked_at: askedAt,
			question_sent: sent,
			masked: counts,
			answered_at: null,
			model_id: null,
			error: null,
			answer: null,
		};
		target.questions.push(question);
		return question;
	}

	private finish(question: Question, outcome: FakeOutcome): void {
		const answeredAt = "2026-11-03T11:31:00Z";
		if (outcome.kind === "failed") {
			question.status = "failed";
			question.error = outcome.error;
			return;
		}
		const isNothing = outcome.kind === "nothing";
		question.status = "answered";
		question.answered_at = answeredAt;
		question.model_id = isNothing ? null : MODEL_ID;
		question.answer = {
			claims: isNothing ? [] : outcome.claims,
			nothing_found: isNothing,
			message: isNothing ? NOTHING_FOUND : null,
			run_id: this.access.latestRunId(question.review_id),
			sources: isNothing ? [] : outcome.sources,
			steps: [],
			rounds: isNothing ? 0 : 1,
			kept_round: isNothing ? 0 : 1,
			stopped_because: isNothing ? null : "accepted",
			warnings: [],
		};
	}

	private find(questionId: unknown): Question | undefined {
		return this.threads
			.flatMap((t) => t.questions)
			.find((q) => q.id === questionId);
	}

	handlers() {
		return [
			http.post(
				`${API}/reviews/:reviewId/questions`,
				async ({ params, request }) => {
					const reached = this.access.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const { review } = reached;
					const body = await jsonBody(request);
					const extra = Object.keys(body).filter(
						(key) => key !== "question" && key !== "thread_id",
					);
					const raw = body["question"];
					const question =
						typeof raw === "string" ? raw.replace(/\s+/g, " ").trim() : "";
					if (
						extra.length > 0 ||
						question === "" ||
						question.length > MAX_QUESTION_LENGTH
					) {
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
					const threadId = body["thread_id"] ?? null;
					const thread =
						threadId === null
							? null
							: this.threads.find(
									(t) => t.id === threadId && t.review_id === review.id,
								);
					if (thread === undefined) {
						return problem(404, "Not Found");
					}
					const last = thread?.questions.at(-1);
					if (last !== undefined && isOpen(last)) {
						return problem(
							409,
							"The last question in this thread is still being answered.",
						);
					}
					if (this.askReply !== null) {
						await this.askReply;
					}
					const asked = this.ask(review, question, thread);
					return HttpResponse.json(asked, {
						status: 202,
						headers: { location: `/api/v1/questions/${asked.id}` },
					});
				},
			),

			http.get(`${API}/reviews/:reviewId/threads`, ({ params }) => {
				const reached = this.access.reach(params["reviewId"], false);
				return reached instanceof Response
					? reached
					: HttpResponse.json(
							this.threads
								.filter((t) => t.review_id === reached.review.id)
								.sort(
									(a, b) =>
										b.created_at.localeCompare(a.created_at) ||
										b.id.localeCompare(a.id),
								),
						);
			}),

			http.get(`${API}/threads/:threadId`, ({ params }) => {
				const thread = this.threads.find((t) => t.id === params["threadId"]);
				const reached = this.access.reach(thread?.review_id, false);
				if (reached instanceof Response) {
					return reached;
				}
				return thread === undefined
					? problem(404, "Not Found")
					: HttpResponse.json(thread);
			}),

			http.get(`${API}/questions/:questionId`, ({ params }) => {
				const question = this.find(params["questionId"]);
				const reached = this.access.reach(question?.review_id, false);
				if (reached instanceof Response) {
					return reached;
				}
				if (question === undefined) {
					return problem(404, "Not Found");
				}
				if (isOpen(question)) {
					if (this.outcome === null) {
						question.status = "answering";
					} else {
						this.finish(question, this.outcome);
					}
				}
				return HttpResponse.json(question);
			}),
		];
	}
}
