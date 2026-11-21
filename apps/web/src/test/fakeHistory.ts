/**
 * Needs you, activity, timelines and seen and saved marks for the fake API,
 * beside fakeInstructions.ts. Tests arrange the items the API would derive;
 * the fake applies what the screens depend on: 404 for non-members, Dismiss
 * removes an item (404 for one not listed), and marks change what is read.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, nextId, noContent, problem, text } from "./fakeHttp";

const API = "*/api/v1";

type Schemas = components["schemas"];
type NeedsYouItem = Schemas["NeedsYouItemOut"];
type ActivityEvent = Schemas["ActivityEventOut"];
type HistoryGroup = Schemas["HistoryGroupOut"];
type PaperMarks = Schemas["PaperMarksOut"];
type SavedPaper = Schemas["SavedPaperOut"];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	projectName: (projectId: string) => string | null;
}

export class FakeHistory {
	readonly needsYou: NeedsYouItem[] = [];
	readonly activity: ActivityEvent[] = [];
	readonly timelines = new Map<string, HistoryGroup[]>();
	/** Each project's marks, by project id. */
	readonly marks = new Map<string, PaperMarks[]>();
	readonly saved: SavedPaper[] = [];
	readonly briefsSeen: string[] = [];

	constructor(private readonly access: Access) {}

	private project(projectId: string) {
		return { id: projectId, name: this.access.projectName(projectId) ?? "" };
	}

	addNeedsYou(
		projectId: string,
		extra: Partial<NeedsYouItem> & Pick<NeedsYouItem, "kind">,
	): NeedsYouItem {
		const target = nextId("t");
		const item: NeedsYouItem = {
			key: `${extra.kind}:${target}`,
			project: this.project(projectId),
			review_id: nextId("r"),
			at: "2026-11-19T09:00:00Z",
			gate_id: null,
			gate_kind: null,
			run_id: target,
			run_number: 1,
			stopped_at: null,
			version: 1,
			brief_id: null,
			deletes_at: null,
			days_left: null,
			...extra,
		};
		this.needsYou.push(item);
		return item;
	}

	/** A project's "Deleted in N days unless kept", as the API lists it for owners. */
	addRetentionNotice(projectId: string, daysLeft: number): NeedsYouItem {
		return this.addNeedsYou(projectId, {
			kind: "retention",
			key: `retention:${projectId}:2026-12-05`,
			review_id: null,
			run_id: null,
			run_number: null,
			version: null,
			deletes_at: "2026-12-05T09:00:00Z",
			days_left: daysLeft,
		});
	}

	/** What a Keep does to Needs you: the project's notice is no longer waiting. */
	forgetRetention(projectId: string): void {
		const index = this.needsYou.findIndex(
			(item) => item.kind === "retention" && item.project.id === projectId,
		);
		if (index >= 0) {
			this.needsYou.splice(index, 1);
		}
	}

	/** A line as the API sends it; only the facts the line uses need be given. */
	event(
		projectId: string,
		extra: Partial<ActivityEvent> & Pick<ActivityEvent, "action" | "kind">,
	): ActivityEvent {
		return {
			at: "2026-11-19T09:00:00Z",
			project: this.project(projectId),
			review_id: nextId("r"),
			actor: null,
			run_id: null,
			run_number: null,
			run_state: null,
			seconds: null,
			stopped_at: null,
			error: null,
			model_ids: [],
			stages: [],
			version: null,
			brief_id: null,
			thread_id: null,
			document_id: null,
			document_number: null,
			text: null,
			papers_moved: null,
			...extra,
		};
	}

	addSaved(projectId: string, extra: Partial<SavedPaper> = {}): SavedPaper {
		const paper: SavedPaper = {
			paper_id: nextId("pa"),
			project: this.project(projectId),
			review_id: nextId("r"),
			title: "Cash transfers and food prices in small Sahel markets",
			year: 2026,
			doi: "10.1016/j.foodpol.2026.102345",
			link: "https://doi.org/10.1016/j.foodpol.2026.102345",
			saved_at: "2026-11-19T09:00:00Z",
			...extra,
		};
		this.saved.push(paper);
		return paper;
	}

	setMarks(projectId: string, marks: PaperMarks): void {
		const list = (this.marks.get(projectId) ?? []).filter(
			(m) => m.paper_id !== marks.paper_id,
		);
		this.marks.set(projectId, [...list, marks]);
	}

	marksOf(projectId: string, paperId: string): PaperMarks {
		return (
			this.marks.get(projectId)?.find((m) => m.paper_id === paperId) ?? {
				paper_id: paperId,
				is_seen: false,
				is_saved: false,
				seen_in_other_projects: [],
			}
		);
	}

	private gate(projectId: string): Response | null {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		return this.access.projectName(projectId) === null
			? problem(404, "No such project.")
			: null;
	}

	handlers() {
		const paper = `${API}/projects/:projectId/papers/:paperId`;
		const ids = (params: Record<string, unknown>) =>
			[String(params["projectId"]), String(params["paperId"])] as const;
		return [
			http.get(`${API}/needs-you`, () =>
				HttpResponse.json({
					count: this.needsYou.length,
					items: this.needsYou,
				}),
			),

			http.post(`${API}/needs-you/dismissals`, async ({ request }) => {
				const key = text(await jsonBody(request), "key");
				const index = this.needsYou.findIndex((item) => item.key === key);
				if (index < 0) {
					return problem(404, "Nothing waiting for you has this key.");
				}
				this.needsYou.splice(index, 1);
				return noContent();
			}),

			http.get(`${API}/activity`, () =>
				HttpResponse.json({ events: this.activity }),
			),

			http.get(`${API}/projects/:projectId/history`, ({ params }) => {
				const projectId = String(params["projectId"]);
				return (
					this.gate(projectId) ??
					HttpResponse.json({ groups: this.timelines.get(projectId) ?? [] })
				);
			}),

			http.get(`${API}/projects/:projectId/paper-marks`, ({ params }) => {
				const projectId = String(params["projectId"]);
				return (
					this.gate(projectId) ??
					HttpResponse.json({ papers: this.marks.get(projectId) ?? [] })
				);
			}),

			http.put(`${paper}/seen`, ({ params }) => {
				const [projectId, paperId] = ids(params);
				const refused = this.gate(projectId);
				if (refused !== null) {
					return refused;
				}
				this.setMarks(projectId, {
					...this.marksOf(projectId, paperId),
					is_seen: true,
				});
				return noContent();
			}),

			http.put(`${paper}/saved`, ({ params }) => {
				const [projectId, paperId] = ids(params);
				const refused = this.gate(projectId);
				if (refused !== null) {
					return refused;
				}
				this.setMarks(projectId, {
					...this.marksOf(projectId, paperId),
					is_saved: true,
				});
				if (!this.saved.some((p) => p.paper_id === paperId)) {
					this.addSaved(projectId, { paper_id: paperId });
				}
				return noContent();
			}),

			http.delete(`${paper}/saved`, ({ params }) => {
				const [projectId, paperId] = ids(params);
				const refused = this.gate(projectId);
				if (refused !== null) {
					return refused;
				}
				this.setMarks(projectId, {
					...this.marksOf(projectId, paperId),
					is_saved: false,
				});
				const index = this.saved.findIndex((p) => p.paper_id === paperId);
				if (index >= 0) {
					this.saved.splice(index, 1);
				}
				return noContent();
			}),

			http.get(`${API}/saved-papers`, () =>
				HttpResponse.json({ papers: this.saved }),
			),

			http.put(`${API}/briefs/:briefId/seen`, ({ params }) => {
				const briefId = String(params["briefId"]);
				this.briefsSeen.push(briefId);
				const index = this.needsYou.findIndex((i) => i.brief_id === briefId);
				if (index >= 0) {
					this.needsYou.splice(index, 1);
				}
				return noContent();
			}),
		];
	}
}
