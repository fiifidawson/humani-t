/**
 * A project's instructions to the model for the fake API, beside fakeDocuments.ts.
 *
 * It applies the rules the screens depend on: 404 for non-members, every member
 * reads, owners save, a save based on an older version is a 409, and each save is
 * a new version. A test sets `refusal` to have every save refused with that 422.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, problem, text } from "./fakeHttp";

const API = "*/api/v1";
const NOT_YOUR_ROLE = "Your role in this project does not allow this.";
const CHANGED =
	"The record changed while you were editing it. Reload and try again.";

type Schemas = components["schemas"];
type Role = Schemas["Role"];
type Version = Schemas["ModelInstructionsVersionOut"];
type UserRef = Schemas["UserRefOut"];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	roleIn: (projectId: string) => Role | null;
	user: () => UserRef | null;
}

export class FakeInstructions {
	readonly versions = new Map<string, Version[]>();
	refusal: string | null = null;

	constructor(private readonly access: Access) {}

	/** A version saved earlier, as PUT would store it. */
	add(projectId: string, extra: Partial<Version> = {}): Version {
		const list = this.versions.get(projectId) ?? [];
		const version: Version = {
			version: list.length + 1,
			screening: "",
			writing: "",
			saved_by: this.access.user() ?? { id: "u-gone", display_name: "Gone" },
			saved_at: "2026-11-13T09:30:00Z",
			...extra,
		};
		list.push(version);
		this.versions.set(projectId, list);
		return version;
	}

	private gate(projectId: string): Response | Role {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		return this.access.roleIn(projectId) ?? problem(404, "Not Found");
	}

	handlers() {
		return [
			http.get(
				`${API}/projects/:projectId/model-instructions`,
				({ params }) => {
					const projectId = String(params["projectId"]);
					const role = this.gate(projectId);
					if (role instanceof Response) {
						return role;
					}
					const versions = this.versions.get(projectId) ?? [];
					return HttpResponse.json({
						current: versions.at(-1) ?? null,
						versions,
					});
				},
			),

			http.put(
				`${API}/projects/:projectId/model-instructions`,
				async ({ params, request }) => {
					const projectId = String(params["projectId"]);
					const role = this.gate(projectId);
					if (role instanceof Response) {
						return role;
					}
					if (role !== "owner") {
						return problem(403, NOT_YOUR_ROLE);
					}
					if (this.refusal !== null) {
						return problem(422, this.refusal);
					}
					const body = await jsonBody(request);
					const latest = this.versions.get(projectId)?.at(-1)?.version ?? null;
					if ((body["based_on_version"] ?? null) !== latest) {
						return problem(409, CHANGED);
					}
					const version = this.add(projectId, {
						screening: text(body, "screening").trim(),
						writing: text(body, "writing").trim(),
						saved_at: new Date().toISOString(),
					});
					return HttpResponse.json(version);
				},
			),
		];
	}
}
