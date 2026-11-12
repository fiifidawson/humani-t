/**
 * Every read the screens make, as TanStack Query options with stable keys.
 *
 * Keeping keys in one place is what makes invalidation reliable: a screen
 * that changes projects invalidates `keys.projects`, and every list showing
 * projects refetches.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";

export const keys = {
	session: ["session"] as const,
	projects: ["projects"] as const,
	projectList: (includeArchived: boolean) =>
		["projects", "list", { includeArchived }] as const,
	/** Both project lists, with archived projects and without. */
	projectLists: ["projects", "list"] as const,
	project: (projectId: string) => ["projects", "one", projectId] as const,
	members: (projectId: string) =>
		["projects", "one", projectId, "members"] as const,
	devices: ["account", "sessions"] as const,
	mfa: ["account", "mfa"] as const,
	reviews: (projectId: string) =>
		["projects", "one", projectId, "reviews"] as const,
	review: (reviewId: string) => ["reviews", reviewId] as const,
	plan: (reviewId: string) => ["reviews", reviewId, "plan"] as const,
	planVersion: (reviewId: string, version: number) =>
		["reviews", reviewId, "plan", "versions", version] as const,
	runs: (reviewId: string) => ["reviews", reviewId, "runs"] as const,
	papers: (reviewId: string, filter: PaperFilter) =>
		["reviews", reviewId, "papers", filter] as const,
	paper: (reviewId: string, paperId: string, runId: string) =>
		["reviews", reviewId, "papers", "one", paperId, runId] as const,
	prisma: (reviewId: string, runId: string) =>
		["reviews", reviewId, "prisma", runId] as const,
	labelRules: (reviewId: string) =>
		["reviews", reviewId, "label-rules"] as const,
	brief: (reviewId: string, runId: string | null) =>
		["reviews", reviewId, "brief", runId] as const,
	evidence: (briefId: string, tag: string) =>
		["briefs", briefId, "evidence", tag] as const,
	trace: (briefId: string) => ["briefs", briefId, "trace"] as const,
	documents: (projectId: string) =>
		["projects", "one", projectId, "documents"] as const,
	maskedDocument: (projectId: string, documentId: string) =>
		["projects", "one", projectId, "documents", documentId, "masked"] as const,
	// Outside the project's keys: refreshing the project or its documents must not
	// fetch raw text again, because the API audits every fetch of it.
	documentReview: (projectId: string, documentId: string) =>
		["raw-text", projectId, "document", documentId] as const,
	maskingRules: (projectId: string) =>
		["raw-text", projectId, "masking-rules"] as const,
	threads: (reviewId: string) => ["reviews", reviewId, "threads"] as const,
	thread: (threadId: string) => ["threads", threadId] as const,
	question: (questionId: string) => ["questions", questionId] as const,
};

export interface PaperFilter {
	runId: string;
	/** include, exclude, uncertain or unscreened; `null` for every paper. */
	label: string | null;
	/** skipped, failed or not_screened; `null` for any. */
	tier: string | null;
	page: number;
}

export const sessionQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: keys.session,
		queryFn: () => unwrap(api.GET("/api/v1/auth/session")),
		staleTime: 60_000,
	});

export const projectListQuery = (api: ApiClient, includeArchived = false) =>
	queryOptions({
		queryKey: keys.projectList(includeArchived),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects", {
					params: { query: { include_archived: includeArchived } },
				}),
			),
	});

export const projectQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: keys.project(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export const membersQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: keys.members(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/members", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export const devicesQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: keys.devices,
		queryFn: () => unwrap(api.GET("/api/v1/account/sessions")),
	});

export const mfaQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: keys.mfa,
		queryFn: () => unwrap(api.GET("/api/v1/account/mfa")),
	});
