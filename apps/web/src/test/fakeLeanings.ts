/**
 * "Your view first" for the fake API: each member's leaning per run's brief.
 *
 * It applies the rules the screens depend on: 404 for non-members and runs without
 * a brief, only your own answer is returned, one answer each, an option must be one
 * of the brief's, and a project that does not ask refuses with 409.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, problem } from "./fakeHttp";

const API = "*/api/v1";

type Leaning = components["schemas"]["LeaningOut"];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	userId: () => string | null;
	/** The run's project, if the caller is a member of it. */
	projectOf: (runId: string) => string | null;
	isViewFirstOn: (projectId: string) => boolean;
	/** The option names of the run's brief, or null when it has none. */
	briefOptions: (runId: string) => string[] | null;
}

export class FakeLeanings {
	readonly answers = new Map<string, Leaning>();

	constructor(private readonly access: Access) {}

	/** An answer given earlier, as PUT would store it. */
	add(runId: string, userId: string, option: string | null): Leaning {
		const leaning = {
			run_id: runId,
			option,
			answered_at: "2026-11-19T09:00:00Z",
		};
		this.answers.set(`${runId}:${userId}`, leaning);
		return leaning;
	}

	private reach(
		runId: string,
	): { userId: string; projectId: string } | Response {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		const userId = this.access.userId();
		const projectId = this.access.projectOf(runId);
		return userId === null || projectId === null
			? problem(404, "No such run.")
			: { userId, projectId };
	}

	handlers() {
		return [
			http.get(`${API}/runs/:runId/leaning`, ({ params }) => {
				const runId = String(params["runId"]);
				const reached = this.reach(runId);
				if (reached instanceof Response) {
					return reached;
				}
				return HttpResponse.json(
					this.answers.get(`${runId}:${reached.userId}`) ?? null,
				);
			}),

			http.put(`${API}/runs/:runId/leaning`, async ({ params, request }) => {
				const runId = String(params["runId"]);
				const reached = this.reach(runId);
				if (reached instanceof Response) {
					return reached;
				}
				if (!this.access.isViewFirstOn(reached.projectId)) {
					return problem(409, "This project does not ask for your view first.");
				}
				const options = this.access.briefOptions(runId);
				if (options === null) {
					return problem(404, "This run has no decision brief.");
				}
				const option = (await jsonBody(request))["option"];
				if (
					option !== null &&
					(typeof option !== "string" || !options.includes(option))
				) {
					return problem(
						422,
						"Leaning.option must be one of the brief's options",
					);
				}
				const key = `${runId}:${reached.userId}`;
				if (this.answers.has(key)) {
					return problem(
						409,
						"You already said which option you leaned towards for this brief.",
					);
				}
				return HttpResponse.json(this.add(runId, reached.userId, option));
			}),
		];
	}
}
