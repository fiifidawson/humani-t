/**
 * The writes behind collections, tags and pins (UI_UX_SPEC §7.3), as hooks.
 *
 * Each one refreshes every project list and the collection counts afterwards:
 * the API decides what changed, and the screens only show it.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { unwrap, unwrapEmpty } from "../../api/client";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";

function useRefresh() {
	const queryClient = useQueryClient();
	return async () => {
		await Promise.all([
			queryClient.invalidateQueries({ queryKey: keys.projects }),
			queryClient.invalidateQueries({ queryKey: keys.collections }),
		]);
	};
}

export function usePin(projectId: string) {
	const api = useApi();
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (isPinned: boolean) => {
			const request = { params: { path: { project_id: projectId } } };
			return unwrapEmpty(
				isPinned
					? api.PUT("/api/v1/projects/{project_id}/pin", request)
					: api.DELETE("/api/v1/projects/{project_id}/pin", request),
			);
		},
		onSuccess: refresh,
	});
}

export function useCreateCollection() {
	const api = useApi();
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (name: string) =>
			unwrap(api.POST("/api/v1/collections", { body: { name } })),
		onSuccess: refresh,
	});
}

/** Move one project, or several through the bulk route; `null` means no collection. */
export function useMoveProjects() {
	const api = useApi();
	const refresh = useRefresh();
	return useMutation({
		mutationFn: ({
			projectIds,
			collectionId,
		}: {
			projectIds: readonly string[];
			collectionId: string | null;
		}) =>
			unwrap(
				api.POST("/api/v1/project-actions", {
					body: {
						action: "move",
						project_ids: [...projectIds],
						collection_id: collectionId,
					},
				}),
			),
		onSuccess: refresh,
	});
}

export function useSetTags(projectId: string) {
	const api = useApi();
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (tags: readonly string[]) =>
			unwrap(
				api.PUT("/api/v1/projects/{project_id}/tags", {
					params: { path: { project_id: projectId } },
					body: { tags: [...tags] },
				}),
			),
		onSuccess: refresh,
	});
}

export function useBulkAction() {
	const api = useApi();
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (
			action:
				| { action: "add_tag"; projectIds: readonly string[]; tag: string }
				| { action: "archive"; projectIds: readonly string[] },
		) =>
			unwrap(
				api.POST("/api/v1/project-actions", {
					body:
						action.action === "add_tag"
							? {
									action: "add_tag",
									project_ids: [...action.projectIds],
									tag: action.tag,
								}
							: { action: "archive", project_ids: [...action.projectIds] },
				}),
			),
		onSuccess: refresh,
	});
}
