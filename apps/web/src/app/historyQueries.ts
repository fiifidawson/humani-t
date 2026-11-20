/**
 * The reads of Needs you, Recent activity, the project timeline and the
 * seen and saved marks (UI_UX_SPEC §7.1, §7.4, §7.5). A project's keys sit
 * under `keys.project`, so refreshing a project refreshes them too.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";
import { keys } from "./queries";

export const historyKeys = {
	needsYou: ["needs-you"] as const,
	activity: ["activity"] as const,
	timeline: (projectId: string) =>
		[...keys.project(projectId), "history"] as const,
	paperMarks: (projectId: string) =>
		[...keys.project(projectId), "paper-marks"] as const,
	saved: ["saved-papers"] as const,
};

export const needsYouQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: historyKeys.needsYou,
		queryFn: () => unwrap(api.GET("/api/v1/needs-you")),
	});

export const activityQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: historyKeys.activity,
		queryFn: () => unwrap(api.GET("/api/v1/activity")),
	});

export const timelineQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: historyKeys.timeline(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/history", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export const paperMarksQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: historyKeys.paperMarks(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/paper-marks", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export const savedPapersQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: historyKeys.saved,
		queryFn: () => unwrap(api.GET("/api/v1/saved-papers")),
	});
