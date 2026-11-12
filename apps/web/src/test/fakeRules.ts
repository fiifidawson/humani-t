/**
 * Label rules for the fake API, served beside fakeReviews.ts.
 *
 * It applies the rules the screens depend on: every member reads, viewers get
 * 403 for preview and apply, nonsense rules get 422. Which papers move is the
 * API's to compute, so a test sets `moves`; applying moves that many papers.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, problem } from "./fakeHttp";

const API = "*/api/v1";

type Schemas = components["schemas"];
type Role = Schemas["Role"];
type Review = Schemas["ReviewOut"];
type Paper = Schemas["PaperOut"];
type Tier = Schemas["ScreeningTier"];
type Version = Schemas["LabelRulesVersionOut"];
type Move = Schemas["TierMoveOut"];

const UNCLEAR: readonly Version["unclear_counts_as"][] = [
	"met",
	"not_met",
	"uncertain",
];

const DECISIONS: Record<
	Tier,
	Pick<Schemas["DecisionOut"], "label" | "priority">
> = {
	highly_relevant: { label: "include", priority: "high" },
	relevant: { label: "include", priority: "medium" },
	possibly_relevant: { label: "include", priority: "low" },
	excluded: { label: "exclude", priority: "low" },
	uncertain: { label: "uncertain", priority: "low" },
};

interface Access {
	reach: (
		reviewId: unknown,
		write: boolean,
	) => { review: Review; role: Role } | Response;
	userId: () => string | null;
	latestRunId: (reviewId: string) => string | null;
	papers: (reviewId: string) => Paper[];
}

export class FakeRules {
	readonly versions = new Map<string, Version[]>();
	/** What the next preview or apply reports. */
	moves: Move[] = [];
	untouchable = 0;
	/** When set, applying waits for it, so a test can see what shows meanwhile. */
	applyReply: Promise<void> | null = null;

	constructor(private readonly access: Access) {}

	/** A version applied earlier, as PUT would store it. */
	add(reviewId: string, extra: Partial<Version> = {}): Version {
		const list = this.versions.get(reviewId) ?? [];
		const version: Version = {
			version: list.length + 1,
			possibly_min_met: 1,
			unclear_counts_as: "not_met",
			changed_by: this.access.userId() ?? "",
			changed_at: "2026-11-03T14:05:00Z",
			papers_moved: 0,
			...extra,
		};
		list.push(version);
		this.versions.set(reviewId, list);
		return version;
	}

	private preview(reviewId: string) {
		return {
			run_id: this.access.latestRunId(reviewId),
			moves: this.moves,
			moved: this.moves.reduce((sum, move) => sum + move.count, 0),
			untouchable: this.untouchable,
		};
	}

	/** Move `count` papers per move, as the API's relabelling would. */
	private relabel(reviewId: string, version: number) {
		const papers = this.access.papers(reviewId);
		for (const move of this.moves) {
			papers
				.filter((paper) => paper.decision?.tier === move.from_tier)
				.slice(0, move.count)
				.forEach((paper) => {
					if (paper.decision !== null) {
						paper.decision = {
							...paper.decision,
							...DECISIONS[move.to_tier],
							tier: move.to_tier,
							label_set_by: "rules",
							rules_version: version,
						};
					}
				});
		}
	}

	handlers() {
		const valid = async (request: Request) => {
			const body = await jsonBody(request);
			const min = body["possibly_min_met"];
			const unclear = UNCLEAR.find((u) => u === body["unclear_counts_as"]);
			return typeof min === "number" &&
				Number.isInteger(min) &&
				min >= 1 &&
				unclear !== undefined
				? { possibly_min_met: min, unclear_counts_as: unclear }
				: null;
		};
		const refused = () =>
			problem(422, "possibly_min_met must be a whole number of at least 1.");

		return [
			http.get(`${API}/reviews/:reviewId/label-rules`, ({ params }) => {
				const reached = this.access.reach(params["reviewId"], false);
				if (reached instanceof Response) {
					return reached;
				}
				const versions = this.versions.get(reached.review.id) ?? [];
				return HttpResponse.json({
					current: versions.at(-1) ?? null,
					versions,
				});
			}),

			http.post(
				`${API}/reviews/:reviewId/label-rules/preview`,
				async ({ params, request }) => {
					const reached = this.access.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					return (await valid(request)) === null
						? refused()
						: HttpResponse.json(this.preview(reached.review.id));
				},
			),

			http.put(
				`${API}/reviews/:reviewId/label-rules`,
				async ({ params, request }) => {
					const reached = this.access.reach(params["reviewId"], true);
					if (reached instanceof Response) {
						return reached;
					}
					const body = await valid(request);
					if (body === null) {
						return refused();
					}
					if (this.applyReply !== null) {
						await this.applyReply;
					}
					const reviewId = reached.review.id;
					const preview = this.preview(reviewId);
					const version = this.add(reviewId, {
						...body,
						papers_moved: preview.moved,
					});
					this.relabel(reviewId, version.version);
					return HttpResponse.json({ ...preview, version });
				},
			),
		];
	}
}
