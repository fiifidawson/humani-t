/**
 * Brief versions and comparisons for the fake API, served beside fakeReviews.ts.
 *
 * It applies the access rules and refuses one id twice or two reviews (422).
 * What changed is the API's to compute, so a test sets `briefChanges` and `runParts`.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { problem } from "./fakeHttp";

const API = "*/api/v1";

type Schemas = components["schemas"];
type Role = Schemas["Role"];
type Review = Schemas["ReviewOut"];
type Run = Schemas["RunOut"];
type Brief = Schemas["BriefOut"];
type BriefChanges = Schemas["BriefChangesOut"];
type RunComparison = Schemas["RunComparisonOut"];

interface Access {
	reach: (
		reviewId: unknown,
		write: boolean,
	) => { review: Review; role: Role } | Response;
	runs: () => readonly Run[];
	briefs: () => readonly Brief[];
}

export const NO_CHANGES: BriefChanges = {
	options_added: [],
	options_removed: [],
	strength_changes: [],
	sources_added: [],
	sources_removed: [],
};

export const SAME_SETUP: NonNullable<RunComparison["setup"]> = {
	vocabulary_versions: { before: [3], after: [3] },
	sources: { before: ["openalex"], after: ["openalex"] },
	model_ids: { before: ["qwen3.5:9b"], after: ["qwen3.5:9b"] },
	queries_added: [],
	queries_removed: [],
	prompt_versions: [{ name: "screen", before: "screen-3", after: "screen-3" }],
	settings_changed: [],
};

type RunParts = Omit<RunComparison, "before" | "after">;

export class FakeComparisons {
	/** What the next brief comparison reports. */
	briefChanges: BriefChanges = NO_CHANGES;
	/** What the next run comparison reports, apart from the two runs. */
	runParts: RunParts = {
		cannot_compare: null,
		setup: SAME_SETUP,
		discovery: {
			before: { identified: 0, after_deduplication: 0 },
			after: { identified: 0, after_deduplication: 0 },
			only_before: [],
			only_after: [],
			in_both: 0,
		},
		screening: { before: {}, after: {}, changed: [] },
		brief: null,
	};

	constructor(private readonly access: Access) {}

	private reachBrief(briefId: unknown) {
		const brief = this.access.briefs().find((b) => b.id === briefId);
		const reached = this.access.reach(brief?.review_id, false);
		return reached instanceof Response || brief === undefined
			? reached instanceof Response
				? reached
				: problem(404, "Not Found")
			: brief;
	}

	private reachRun(runId: unknown) {
		const run = this.access.runs().find((r) => r.id === runId);
		const reached = this.access.reach(run?.review_id, false);
		return reached instanceof Response || run === undefined
			? reached instanceof Response
				? reached
				: problem(404, "Not Found")
			: run;
	}

	/** The run's place among its review's runs, from 1, oldest first. */
	private numberOf(run: Run): number {
		const runs = this.access
			.runs()
			.filter((r) => r.review_id === run.review_id);
		return runs.length - runs.indexOf(run);
	}

	handlers() {
		return [
			http.get(`${API}/reviews/:reviewId/brief/versions`, ({ params }) => {
				const reached = this.access.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const briefs = this.access
					.briefs()
					.filter((b) => b.review_id === reached.review.id)
					.sort((a, b) => b.version - a.version);
				return HttpResponse.json(
					briefs.map((b, index) => ({
						id: b.id,
						version: b.version,
						run_id: b.run_id,
						created_at: b.created_at,
						model_id: b.model_id,
						is_latest: index === 0,
					})),
				);
			}),

			http.get(`${API}/briefs/:briefId/compare/:otherId`, ({ params }) => {
				const first = this.reachBrief(params["briefId"]);
				const second = this.reachBrief(params["otherId"]);
				if (first instanceof Response) {
					return first;
				}
				if (second instanceof Response) {
					return second;
				}
				if (first.id === second.id || first.review_id !== second.review_id) {
					return problem(422, "Choose two different versions to compare.");
				}
				const [before, after] = [first, second].sort(
					(a, b) => a.version - b.version,
				) as [Brief, Brief];
				const side = (b: Brief) => ({
					id: b.id,
					version: b.version,
					run_id: b.run_id,
					created_at: b.created_at,
				});
				return HttpResponse.json({
					before: side(before),
					after: side(after),
					...this.briefChanges,
				});
			}),

			http.get(`${API}/runs/:runId/compare/:otherId`, ({ params }) => {
				const first = this.reachRun(params["runId"]);
				const second = this.reachRun(params["otherId"]);
				if (first instanceof Response) {
					return first;
				}
				if (second instanceof Response) {
					return second;
				}
				if (first.id === second.id || first.review_id !== second.review_id) {
					return problem(422, "Choose two different runs to compare.");
				}
				const [before, after] = [first, second].sort(
					(a, b) => this.numberOf(a) - this.numberOf(b),
				) as [Run, Run];
				const side = (r: Run) => ({
					run_id: r.id,
					number: this.numberOf(r),
					created_at: r.created_at,
					has_record: this.runParts.cannot_compare === null,
				});
				const body: RunComparison = {
					before: side(before),
					after: side(after),
					...this.runParts,
				};
				return HttpResponse.json(body);
			}),
		];
	}
}
