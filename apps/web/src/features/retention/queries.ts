/**
 * The reads and the Keep of the retention period (UI_UX_SPEC §7.7). A
 * project's date sits under `keys.project`, so refreshing a project
 * refreshes it too.
 */

import {
	queryOptions,
	useMutation,
	useQueryClient,
} from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../../api/client";
import { useApi } from "../../app/api-context";
import { historyKeys } from "../../app/historyQueries";
import { keys } from "../../app/queries";

export const retentionKeys = {
	policy: ["retention"] as const,
	project: (projectId: string) =>
		[...keys.project(projectId), "retention"] as const,
};

export const retentionPolicyQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: retentionKeys.policy,
		queryFn: () => unwrap(api.GET("/api/v1/retention")),
	});

export const projectRetentionQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: retentionKeys.project(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/retention", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

/** Keep a project: the API restarts its clock, and its notice leaves Needs you. */
export function useKeepProject() {
	const api = useApi();
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (projectId: string) =>
			unwrap(
				api.POST("/api/v1/projects/{project_id}/retention/keep", {
					params: { path: { project_id: projectId } },
				}),
			),
		onSuccess: async (retention, projectId) => {
			queryClient.setQueryData(retentionKeys.project(projectId), retention);
			await queryClient.invalidateQueries({ queryKey: historyKeys.needsYou });
		},
	});
}
