/**
 * Collections, tags, pins and bulk actions for the fake API, beside fakeInstructions.ts.
 *
 * It applies the rules the screens depend on (ADR 0016): every signed-in person sees
 * every collection, counts cover their own projects only, owners file and tag,
 * any member pins, and a bulk action skips what the caller may not change.
 */

import { HttpResponse, http, type JsonBodyType } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, nextId, noContent, problem, text } from "./fakeHttp";

const API = "*/api/v1";
const NOT_YOUR_ROLE = "Your role in this project does not allow this.";
const NO_COLLECTION = "No such collection.";

type Schemas = components["schemas"];

/** The project fields this module reads and changes. */
export interface Organised {
	id: string;
	name: string;
	role: Schemas["Role"];
	archived_at: string | null;
	collection_id: string | null;
	tags: string[];
	pinned_at: string | null;
}

interface FakeCollection {
	id: string;
	name: string;
	can_manage: boolean;
	created_at: string;
}

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	projects: () => Organised[];
	/** The project as `ProjectOut`, after a change. */
	projectOut: (projectId: string) => JsonBodyType;
}

const key = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase();

export class FakeCollections {
	readonly collections: FakeCollection[] = [];

	constructor(private readonly access: Access) {}

	/** A collection made earlier; someone else's unless `isMine`. */
	add(name: string, isMine = true): FakeCollection {
		const collection = {
			id: nextId("c"),
			name,
			can_manage: isMine,
			created_at: "2026-11-10T09:00:00Z",
		};
		this.collections.push(collection);
		return collection;
	}

	private out(collection: FakeCollection): Schemas["CollectionOut"] {
		const count = this.access
			.projects()
			.filter(
				(p) => p.collection_id === collection.id && p.archived_at === null,
			).length;
		return { ...collection, project_count: count };
	}

	private owned(projectId: unknown): Organised | Response {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		const project = this.access.projects().find((p) => p.id === projectId);
		if (project === undefined) {
			return problem(404, "Not Found");
		}
		return project.role === "owner" ? project : problem(403, NOT_YOUR_ROLE);
	}

	private taken(name: string, except: string | null): boolean {
		return this.collections.some(
			(c) => c.id !== except && key(c.name) === key(name),
		);
	}

	handlers() {
		return [
			http.get(`${API}/collections`, () => {
				const refused = this.access.refuseUnlessSignedIn();
				return (
					refused ??
					HttpResponse.json(
						[...this.collections]
							.sort((a, b) => a.name.localeCompare(b.name))
							.map((c) => this.out(c)),
					)
				);
			}),

			http.post(`${API}/collections`, async ({ request }) => {
				const refused = this.access.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const name = text(await jsonBody(request), "name").trim();
				if (name === "") {
					return problem(422, "Collection.name must not be blank");
				}
				if (this.taken(name, null)) {
					return problem(409, "A collection with this name already exists.", {
						collection_name_taken: true,
					});
				}
				return HttpResponse.json(this.out(this.add(name)), { status: 201 });
			}),

			http.delete(`${API}/collections/:collectionId`, ({ params }) => {
				const collection = this.collections.find(
					(c) => c.id === params["collectionId"],
				);
				if (collection === undefined) {
					return problem(404, NO_COLLECTION);
				}
				this.collections.splice(this.collections.indexOf(collection), 1);
				for (const project of this.access.projects()) {
					if (project.collection_id === collection.id) {
						project.collection_id = null;
					}
				}
				return noContent();
			}),

			http.put(
				`${API}/projects/:projectId/collection`,
				async ({ params, request }) => {
					const project = this.owned(params["projectId"]);
					if (project instanceof Response) {
						return project;
					}
					const wanted = (await jsonBody(request))["collection_id"];
					const target =
						typeof wanted === "string"
							? this.collections.find((c) => c.id === wanted)
							: null;
					if (target === undefined) {
						return problem(422, NO_COLLECTION);
					}
					project.collection_id = target?.id ?? null;
					return HttpResponse.json(this.access.projectOut(project.id));
				},
			),

			http.put(
				`${API}/projects/:projectId/tags`,
				async ({ params, request }) => {
					const project = this.owned(params["projectId"]);
					if (project instanceof Response) {
						return project;
					}
					const tags = (await jsonBody(request))["tags"];
					const typed = Array.isArray(tags) ? tags.map(String) : [];
					if (typed.some((tag) => tag.trim().length > 40)) {
						return problem(422, "tag must be at most 40 characters");
					}
					project.tags = dedupe(typed);
					return HttpResponse.json(this.access.projectOut(project.id));
				},
			),

			http.put(`${API}/projects/:projectId/pin`, ({ params }) =>
				this.pin(params["projectId"], true),
			),

			http.delete(`${API}/projects/:projectId/pin`, ({ params }) =>
				this.pin(params["projectId"], false),
			),

			http.post(`${API}/project-actions`, async ({ request }) => {
				const refused = this.access.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const body = await jsonBody(request);
				const ids = Array.isArray(body["project_ids"])
					? body["project_ids"].map(String)
					: [];
				const wanted = body["collection_id"];
				if (
					body["action"] === "move" &&
					typeof wanted === "string" &&
					!this.collections.some((c) => c.id === wanted)
				) {
					return problem(422, NO_COLLECTION);
				}
				const changed: string[] = [];
				const skipped: Schemas["SkippedOut"][] = [];
				for (const id of ids) {
					const project = this.access.projects().find((p) => p.id === id);
					if (project === undefined) {
						skipped.push({ project_id: id, reason: "not_found" });
						continue;
					}
					if (project.role !== "owner") {
						skipped.push({ project_id: id, reason: "not_allowed" });
						continue;
					}
					if (body["action"] === "move") {
						project.collection_id = typeof wanted === "string" ? wanted : null;
					} else if (body["action"] === "add_tag") {
						project.tags = dedupe([...project.tags, text(body, "tag")]);
					} else {
						project.archived_at ??= "2026-11-19T10:00:00Z";
					}
					changed.push(id);
				}
				return HttpResponse.json({ changed, skipped });
			}),
		];
	}

	private pin(projectId: unknown, isPinned: boolean) {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		const project = this.access.projects().find((p) => p.id === projectId);
		if (project === undefined) {
			return problem(404, "Not Found");
		}
		project.pinned_at = isPinned
			? (project.pinned_at ?? new Date().toISOString())
			: null;
		return noContent();
	}
}

function dedupe(tags: readonly string[]): string[] {
	const kept = new Map<string, string>();
	for (const tag of tags) {
		const clean = tag.trim().replace(/\s+/g, " ");
		if (clean !== "" && !kept.has(clean.toLowerCase())) {
			kept.set(clean.toLowerCase(), clean);
		}
	}
	return [...kept.values()];
}
