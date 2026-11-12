/**
 * Going Home once a project is out of reach: deleted, left, or access lost.
 *
 * Only the cache and the route change here; the API has already decided the
 * person can no longer see the project.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { keys } from "../../app/queries";
import type { Project } from "./format";

/** Drop the project from every cached list and query, then go Home. */
export function useLeaveForHome(projectId: string): () => Promise<void> {
	const queryClient = useQueryClient();
	const navigate = useNavigate();
	return async () => {
		// The lists lose it before Home renders, so the rail never shows it again.
		queryClient.setQueriesData<Project[]>(
			{ queryKey: keys.projectLists },
			(projects) => projects?.filter((p) => p.id !== projectId),
		);
		await navigate({ to: "/" });
		queryClient.removeQueries({ queryKey: keys.project(projectId) });
		await queryClient.invalidateQueries({ queryKey: keys.projects });
	};
}
