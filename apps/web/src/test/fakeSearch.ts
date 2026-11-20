/**
 * `GET /search` for the fake API, beside fakeInstructions.ts. It matches for real:
 * a text matches when it holds every word, ignoring case, as the API does. It
 * searches project names and the hits a test adds, only in the person's projects.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { problem } from "./fakeHttp";

const API = "*/api/v1";

type Schemas = components["schemas"];
type Hit = Schemas["SearchHitOut"];
type Scope = Schemas["SearchScope"];

const SCOPES: readonly Scope[] = [
	"projects",
	"briefs",
	"papers",
	"passages",
	"questions",
];

interface SearchableProject {
	id: string;
	name: string;
	created_at: string;
	archived_at: string | null;
}

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	projects: () => readonly SearchableProject[];
}

const EMPTY_HIT: Omit<Hit, "scope" | "text"> = {
	review_id: null,
	run_id: null,
	brief_version: null,
	paper_id: null,
	tag: null,
	title: null,
	thread_id: null,
	question_id: null,
};

function matches(query: string, text: string): boolean {
	const folded = text.toLowerCase();
	return query
		.toLowerCase()
		.split(" ")
		.every((word) => folded.includes(word));
}

export class FakeSearch {
	/** Hits a project's text would give, by project id; names are searched as they are. */
	readonly hits = new Map<string, Hit[]>();
	/** Seconds to refuse the next search for, as `Retry-After`. */
	throttleNext: number | null = null;
	isTruncated = false;
	/** Each search as asked, in order. */
	readonly requested: { q: string; scope: string | null }[] = [];

	constructor(private readonly access: Access) {}

	add(projectId: string, hit: Pick<Hit, "scope" | "text"> & Partial<Hit>): Hit {
		const full: Hit = { ...EMPTY_HIT, ...hit };
		this.hits.set(projectId, [...(this.hits.get(projectId) ?? []), full]);
		return full;
	}

	handlers() {
		return [
			http.get(`${API}/search`, ({ request }) => {
				const refused = this.access.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				if (this.throttleNext !== null) {
					const seconds = this.throttleNext;
					this.throttleNext = null;
					return problem(
						429,
						"Too many attempts. Try again later.",
						{},
						{ "retry-after": String(seconds) },
					);
				}
				const params = new URL(request.url).searchParams;
				this.requested.push({
					q: params.get("q") ?? "",
					scope: params.get("scope"),
				});
				const query = (params.get("q") ?? "").split(/\s+/).join(" ").trim();
				const scopeParam = params.get("scope");
				const scope = SCOPES.find((s) => s === scopeParam) ?? null;
				if (query.length < 2 || query.length > 200) {
					return problem(422, "SearchQuery.text must be 2 to 200 characters");
				}
				if (scopeParam !== null && scope === null) {
					return problem(422, "The request is not valid.");
				}
				const groups = [...this.access.projects()]
					.sort((a, b) => b.created_at.localeCompare(a.created_at))
					.map((project) => ({
						project_id: project.id,
						project_name: project.name,
						is_archived: project.archived_at !== null,
						hits: [
							{ ...EMPTY_HIT, scope: "projects" as const, text: project.name },
							...(this.hits.get(project.id) ?? []),
						].filter(
							(hit) =>
								(scope === null || hit.scope === scope) &&
								matches(query, `${hit.title ?? ""} ${hit.text}`),
						),
					}))
					.filter((group) => group.hits.length > 0);
				return HttpResponse.json({
					query,
					scope,
					groups,
					is_truncated: this.isTruncated,
				});
			}),
		];
	}
}
