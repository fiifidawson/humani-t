/**
 * The reads of brief versions and comparisons (UI_UX_SPEC §6.9 Versions, §7.4).
 * Every comparison is computed by the API; these only fetch it. Versions sit
 * under the review's brief key, so whatever refreshes the brief refreshes them.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";

export const briefVersionsQuery = (api: ApiClient, reviewId: string) =>
	queryOptions({
		queryKey: ["reviews", reviewId, "brief", "versions"] as const,
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/brief/versions", {
					params: { path: { review_id: reviewId } },
				}),
			),
	});

/** Briefs are never edited, so a comparison of two never changes. */
export const briefComparisonQuery = (
	api: ApiClient,
	briefId: string,
	otherId: string,
) =>
	queryOptions({
		queryKey: ["briefs", briefId, "compare", otherId] as const,
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/briefs/{brief_id}/compare/{other_brief_id}", {
					params: { path: { brief_id: briefId, other_brief_id: otherId } },
				}),
			),
		staleTime: Number.POSITIVE_INFINITY,
	});

export const runComparisonQuery = (
	api: ApiClient,
	runId: string,
	otherId: string,
) =>
	queryOptions({
		queryKey: ["runs", runId, "compare", otherId] as const,
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/runs/{run_id}/compare/{other_run_id}", {
					params: { path: { run_id: runId, other_run_id: otherId } },
				}),
			),
	});
