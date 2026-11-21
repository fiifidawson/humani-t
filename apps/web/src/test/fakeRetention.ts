/**
 * The retention period, each project's date and Keep for the fake API, beside
 * fakeHistory.ts. Tests set the period and a project's date; the fake applies
 * what the screens depend on: 404 for non-members, Keep for owners only, 409
 * with no period, and a Keep that restarts the clock and clears the notice.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { problem } from "./fakeHttp";

const API = "*/api/v1";
const DAY = 86_400_000;
/** When a Keep lands in the fake: the fake's own "now". */
export const KEPT_AT = "2026-11-21T10:00:00Z";

type ProjectRetention = components["schemas"]["ProjectRetentionOut"];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	roleIn: (projectId: string) => string | null;
	forgetNotices: (projectId: string) => void;
}

export class FakeRetention {
	/** The deployment's period; `null` deletes nothing for age, as by default. */
	periodDays: number | null = null;
	private readonly dates = new Map<
		string,
		Pick<ProjectRetention, "deletes_at" | "days_left" | "kept_at">
	>();

	constructor(private readonly access: Access) {}

	/** Give a project its date, as the API works it out from its activity. */
	setDate(projectId: string, deletesAt: string, daysLeft: number): void {
		this.dates.set(projectId, {
			deletes_at: deletesAt,
			days_left: daysLeft,
			kept_at: null,
		});
	}

	retentionOf(projectId: string): ProjectRetention {
		const date = this.dates.get(projectId);
		return {
			period_days: this.periodDays,
			deletes_at: this.periodDays === null ? null : (date?.deletes_at ?? null),
			days_left: this.periodDays === null ? null : (date?.days_left ?? null),
			kept_at: date?.kept_at ?? null,
		};
	}

	private gate(projectId: string, isOwnersOnly = false): Response | null {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		const role = this.access.roleIn(projectId);
		if (role === null) {
			return problem(404, "No such project.");
		}
		return isOwnersOnly && role !== "owner"
			? problem(403, "Your role in this project does not allow this.")
			: null;
	}

	handlers() {
		return [
			http.get(`${API}/retention`, () => {
				const refused = this.access.refuseUnlessSignedIn();
				return (
					refused ??
					HttpResponse.json({ period_days: this.periodDays, warning_days: 14 })
				);
			}),

			http.get(`${API}/projects/:projectId/retention`, ({ params }) => {
				const projectId = String(params["projectId"]);
				return (
					this.gate(projectId) ?? HttpResponse.json(this.retentionOf(projectId))
				);
			}),

			http.post(`${API}/projects/:projectId/retention/keep`, ({ params }) => {
				const projectId = String(params["projectId"]);
				const refused = this.gate(projectId, true);
				if (refused !== null) {
					return refused;
				}
				if (this.periodDays === null) {
					return problem(
						409,
						"This deployment does not delete projects for age, so there is nothing to keep.",
					);
				}
				const deletesAt = new Date(
					Date.parse(KEPT_AT) + this.periodDays * DAY,
				).toISOString();
				this.dates.set(projectId, {
					deletes_at: deletesAt,
					days_left: this.periodDays,
					kept_at: KEPT_AT,
				});
				this.access.forgetNotices(projectId);
				return HttpResponse.json(this.retentionOf(projectId));
			}),
		];
	}
}
