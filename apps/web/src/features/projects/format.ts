import type { components } from "../../api/schema.gen";

export type Project = components["schemas"]["ProjectOut"];
export type Role = components["schemas"]["Role"];

const ROLE_LABELS: Readonly<Record<Role, string>> = {
	owner: "Owner",
	reviewer: "Reviewer",
	viewer: "Viewer",
};

export const roleLabel = (role: Role): string => ROLE_LABELS[role];

const DATE = new Intl.DateTimeFormat("en-GB", {
	day: "numeric",
	month: "short",
	year: "numeric",
});
const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
	dateStyle: "long",
	timeStyle: "short",
});

/** "2 Nov 2026"; the full date and time go in the <time> title for hover and screen readers. */
export const shortDate = (iso: string): string => DATE.format(new Date(iso));
export const fullDate = (iso: string): string =>
	DATE_TIME.format(new Date(iso));

export const newestFirst = (projects: readonly Project[]): Project[] =>
	[...projects].sort((a, b) => b.created_at.localeCompare(a.created_at));
