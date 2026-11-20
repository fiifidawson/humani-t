/**
 * The All projects page's state in its URL (UI_UX_SPEC §7.2), and how it groups.
 *
 * Filters and sort go to the API, which decides which projects match. Grouping
 * and the view are only how the page lays out what came back.
 */

import type { ProjectListFilters } from "../../app/queries";
import type { ProjectSummary } from "./format";
import { type ProjectStatus, STATUS_LABELS } from "./ProjectStatusBadge";

export type ListView = "cards" | "list";
export type GroupBy = "collection" | "tag" | "status" | "owner" | "none";
export type SortBy = "last_activity" | "name" | "created" | "relevant_papers";

export const GROUPS: readonly (readonly [GroupBy, string])[] = [
	["collection", "Collection"],
	["tag", "Tag"],
	["status", "Status"],
	["owner", "Owner"],
	["none", "None"],
];

export const SORTS: readonly (readonly [SortBy, string])[] = [
	["last_activity", "Last activity"],
	["name", "Name"],
	["created", "Created"],
	["relevant_papers", "Most relevant papers"],
];

// The statuses §7.2 offers as filters; the others still show on each project.
export const STATUS_FILTERS: readonly ProjectStatus[] = [
	"planning",
	"running",
	"needs_you",
	"brief_ready",
	"failed",
	"archived",
];

export interface ProjectsSearch {
	view?: ListView | undefined;
	group?: GroupBy | undefined;
	sort?: SortBy | undefined;
	status?: ProjectStatus[] | undefined;
	tag?: string[] | undefined;
	owner?: string | undefined;
	member?: string | undefined;
	from?: string | undefined;
	to?: string | undefined;
	/** A collection id, or "none". */
	collection?: string | undefined;
	archived?: boolean | undefined;
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const STATUSES = Object.keys(STATUS_LABELS);

const oneOf = <T extends string>(
	choices: readonly (readonly [T, string])[],
	value: unknown,
): T | undefined => choices.find(([choice]) => choice === value)?.[0];

const text = (value: unknown): string | undefined =>
	typeof value === "string" && value.trim() !== "" ? value : undefined;

const isStatus = (value: unknown): value is ProjectStatus =>
	typeof value === "string" && STATUSES.includes(value);

function texts(value: unknown): string[] | undefined {
	const list = Array.isArray(value) ? value : [value];
	const kept = list.flatMap((item: unknown) => text(item) ?? []);
	return kept.length === 0 ? undefined : kept;
}

/** Every key is returned, undefined when rejected: search values are input like any other. */
export function parseProjectsSearch(
	search: Record<string, unknown>,
): ProjectsSearch {
	const statuses = (
		Array.isArray(search["status"]) ? search["status"] : [search["status"]]
	).filter(isStatus);
	const day = (value: unknown) =>
		typeof value === "string" && DAY.test(value) ? value : undefined;
	return {
		view: search["view"] === "list" ? "list" : undefined,
		group: oneOf(GROUPS, search["group"]),
		sort: oneOf(SORTS, search["sort"]),
		status: statuses.length === 0 ? undefined : statuses,
		tag: texts(search["tag"]),
		owner: text(search["owner"]),
		member: text(search["member"]),
		from: day(search["from"]),
		to: day(search["to"]),
		collection: text(search["collection"]),
		archived: search["archived"] === true ? true : undefined,
	};
}

/** What the API is asked for; keys left out mean "any". */
export function filtersFor(search: ProjectsSearch): ProjectListFilters {
	return {
		sort: search.sort ?? "last_activity",
		include_archived: search.archived === true,
		...(search.status === undefined ? {} : { status: search.status }),
		...(search.tag === undefined ? {} : { tag: search.tag }),
		...(search.owner === undefined ? {} : { owner: search.owner }),
		...(search.member === undefined ? {} : { member: search.member }),
		...(search.from === undefined ? {} : { created_from: search.from }),
		...(search.to === undefined ? {} : { created_to: search.to }),
		...(search.collection === undefined
			? {}
			: { collection: search.collection }),
	};
}

export interface Placed {
	project: ProjectSummary;
	/** The other groups it also shows under. */
	alsoIn: string[];
}

export interface ProjectGroup {
	key: string;
	label: string;
	projects: Placed[];
}

const NO_COLLECTION = "Not in a collection";
const NO_TAGS = "No tags";

/** Groups in the order they show; a project under several keys is in each, marked. */
export function groupProjects(
	projects: readonly ProjectSummary[],
	groupBy: GroupBy,
	collectionNames: ReadonlyMap<string, string>,
): ProjectGroup[] {
	if (groupBy === "none") {
		return [
			{
				key: "all",
				label: "All projects",
				projects: projects.map((project) => ({ project, alsoIn: [] })),
			},
		];
	}
	const groups = new Map<string, ProjectGroup>();
	for (const project of projects) {
		const keys = keysOf(project, groupBy, collectionNames);
		for (const [key, label] of keys) {
			let group = groups.get(key);
			if (group === undefined) {
				group = { key, label, projects: [] };
				groups.set(key, group);
			}
			const alsoIn = keys.filter(([other]) => other !== key).map(([, l]) => l);
			group.projects.push({ project, alsoIn });
		}
	}
	return [...groups.values()].sort(byGroupOrder(groupBy));
}

type Key = readonly [key: string, label: string];

function keysOf(
	project: ProjectSummary,
	groupBy: Exclude<GroupBy, "none">,
	collectionNames: ReadonlyMap<string, string>,
): Key[] {
	switch (groupBy) {
		case "collection":
			return [
				project.collection_id === null
					? ["", NO_COLLECTION]
					: [
							project.collection_id,
							collectionNames.get(project.collection_id) ?? "A collection",
						],
			];
		case "tag":
			return project.tags.length === 0
				? [["", NO_TAGS]]
				: project.tags.map((tag) => [tag.toLocaleLowerCase(), tag] as const);
		case "status":
			return [[project.status, STATUS_LABELS[project.status]]];
		case "owner":
			return project.members
				.filter((m) => m.role === "owner")
				.map((m) => [m.user_id, m.display_name] as const);
	}
}

const STATUS_ORDER = Object.keys(STATUS_LABELS);

function byGroupOrder(groupBy: GroupBy) {
	return (a: ProjectGroup, b: ProjectGroup): number => {
		if (groupBy === "status") {
			return STATUS_ORDER.indexOf(a.key) - STATUS_ORDER.indexOf(b.key);
		}
		// "Not in a collection" and "No tags" come last.
		if ((a.key === "") !== (b.key === "")) {
			return a.key === "" ? 1 : -1;
		}
		return a.label.localeCompare(b.label, undefined, { sensitivity: "base" });
	};
}
