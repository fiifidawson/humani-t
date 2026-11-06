/**
 * The reads of the brief screens (UI_UX_SPEC §6.9 to 6.11). A brief is
 * written once and never edited (docs/api.md "Decision briefs"), so its
 * evidence and trace are never refetched once loaded.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";
import { keys } from "./queries";

/** The review's latest brief, or the one `runId` wrote. */
export const briefQuery = (
	api: ApiClient,
	reviewId: string,
	runId: string | undefined,
) =>
	queryOptions({
		queryKey: keys.brief(reviewId, runId ?? null),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/brief", {
					params: {
						path: { review_id: reviewId },
						query: runId === undefined ? {} : { run_id: runId },
					},
				}),
			),
	});

export const evidenceQuery = (api: ApiClient, briefId: string, tag: string) =>
	queryOptions({
		queryKey: keys.evidence(briefId, tag),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/briefs/{brief_id}/evidence/{tag}", {
					params: { path: { brief_id: briefId, tag } },
				}),
			),
		staleTime: Number.POSITIVE_INFINITY,
	});

export const traceQuery = (api: ApiClient, briefId: string) =>
	queryOptions({
		queryKey: keys.trace(briefId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/briefs/{brief_id}/trace", {
					params: { path: { brief_id: briefId } },
				}),
			),
		staleTime: Number.POSITIVE_INFINITY,
	});
