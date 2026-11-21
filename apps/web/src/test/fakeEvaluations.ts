/**
 * Evaluations for the fake API (routes/evaluations.py): administrators only (403), and
 * only for projects they are members of (404); a viewer may not start one (403).
 * The upload's format is the API's to check; the fake refuses only an empty question file.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, nextId, problem } from "./fakeHttp";

const API = "*/api/v1";

type Evaluation = components["schemas"]["EvaluationOut"];
type Role = "owner" | "reviewer" | "viewer";

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	isAdmin: () => boolean;
	/** The signed-in person's role in the project, or null when not a member. */
	roleIn: (projectId: string) => Role | null;
}

const SCORES = {
	coverage: 1,
	groundedness: 1,
	disagreements: 1,
	uncertainty: 1,
	humanitarian_awareness: 1,
	specificity: 1,
};

/** A finished evaluation of one question against "System B", for arranging tests. */
export function finishedEvaluation(
	projectId: string,
	overrides: Partial<Evaluation> = {},
): Evaluation {
	return {
		id: nextId("e"),
		project_id: projectId,
		status: "finished",
		judge_model: "mistral-small:24b",
		writer_model: "qwen3.5:9b",
		question_count: 1,
		answered_count: 1,
		system_count: 1,
		created_at: "2026-11-21T09:00:00Z",
		finished_at: "2026-11-21T09:20:00Z",
		error: null,
		systems: ["humani-t", "System B"],
		is_masked: true,
		masked: {},
		judge_prompt_version: "evaluation-judge/1",
		results: [
			{
				first: "humani-t",
				second: "System B",
				judged: 1,
				metrics: Object.keys(SCORES).map((metric) => ({
					metric,
					first_mean: 2,
					second_mean: 0.5,
					difference: 1.5,
				})),
				wins: 1,
				ties: 0,
				losses: 0,
			},
		],
		answers: [
			{
				position: 1,
				question: "Do cash transfers raise prices near [LOCATION_1]?",
				answer: "Prices rose in small markets.",
			},
		],
		judgements: [
			{
				position: 1,
				question: "Do cash transfers raise prices near [LOCATION_1]?",
				system_a: "System B",
				system_b: "humani-t",
				answer_a: "Yes, by a tenth.",
				answer_b: "Prices rose in small markets.",
				scores_a: { ...SCORES, coverage: 0 },
				scores_b: { ...SCORES, coverage: 2 },
				winner: "b",
				reason: "B names where prices rose.",
			},
		],
		...overrides,
	};
}

export class FakeEvaluations {
	readonly all: Evaluation[] = [];
	writerModel = "qwen3.5:9b";
	judgeModels = ["qwen3.5:9b", "mistral-small:24b"];

	constructor(private readonly access: Access) {}

	add(evaluation: Evaluation): Evaluation {
		this.all.push(evaluation);
		return evaluation;
	}

	private refuse(): Response | null {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		return this.access.isAdmin() ? null : problem(403, "Administrators only.");
	}

	private reachable(evaluation: Evaluation): boolean {
		return this.access.roleIn(evaluation.project_id) !== null;
	}

	handlers() {
		return [
			http.get(`${API}/admin/evaluations/judges`, () => {
				return (
					this.refuse() ??
					HttpResponse.json({
						writer_model: this.writerModel,
						judge_models: this.judgeModels,
					})
				);
			}),
			http.get(`${API}/admin/evaluations`, () => {
				return (
					this.refuse() ??
					HttpResponse.json(
						this.all
							.filter((e) => this.reachable(e))
							.sort((a, b) => b.created_at.localeCompare(a.created_at)),
					)
				);
			}),
			http.get(`${API}/admin/evaluations/:evaluationId`, ({ params }) => {
				const refused = this.refuse();
				if (refused !== null) {
					return refused;
				}
				const found = this.all.find((e) => e.id === params["evaluationId"]);
				return found === undefined || !this.reachable(found)
					? problem(404, "No such evaluation.")
					: HttpResponse.json(found);
			}),
			http.post(`${API}/admin/evaluations`, async ({ request }) => {
				const refused = this.refuse();
				if (refused !== null) {
					return refused;
				}
				const body = await jsonBody(request);
				const projectId = String(body["project_id"]);
				const role = this.access.roleIn(projectId);
				if (role === null) {
					return problem(404, "No such project.");
				}
				if (role === "viewer") {
					return problem(403, "Your role in this project does not allow this.");
				}
				const judge = String(body["judge_model"]);
				if (!this.judgeModels.includes(judge)) {
					return problem(422, "Pick a judge model this deployment offers.");
				}
				const questions = String(body["question_file"])
					.split("\n")
					.filter((line) => line.trim() !== "");
				if (questions.length === 0) {
					return problem(
						422,
						"The question file has no questions. Put one question on each line.",
					);
				}
				const others = Array.isArray(body["other_systems"])
					? body["other_systems"].map((s: { name?: unknown }) => String(s.name))
					: [];
				const evaluation = this.add({
					...finishedEvaluation(projectId),
					status: "queued",
					judge_model: judge,
					writer_model: null,
					question_count: questions.length,
					answered_count: 0,
					system_count: others.length,
					finished_at: null,
					systems: ["humani-t", ...others],
					results: [],
					answers: [],
					judgements: [],
				});
				return HttpResponse.json(evaluation, {
					status: 202,
					headers: { location: `/api/v1/admin/evaluations/${evaluation.id}` },
				});
			}),
		];
	}
}
