/**
 * The admin area's evaluation reads (UI_UX_SPEC §6.15). An evaluation is judged in
 * the background, so one that is still open is polled until it ends.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";

const POLL_MS = 5000;

export const evaluationKeys = {
	all: ["admin", "evaluations"] as const,
	judges: ["admin", "evaluations", "judges"] as const,
	one: (evaluationId: string) =>
		["admin", "evaluations", "one", evaluationId] as const,
};

export const evaluationListQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: evaluationKeys.all,
		queryFn: () => unwrap(api.GET("/api/v1/admin/evaluations")),
	});

export const judgeModelsQuery = (api: ApiClient) =>
	queryOptions({
		queryKey: evaluationKeys.judges,
		queryFn: () => unwrap(api.GET("/api/v1/admin/evaluations/judges")),
	});

export const evaluationQuery = (api: ApiClient, evaluationId: string) =>
	queryOptions({
		queryKey: evaluationKeys.one(evaluationId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/admin/evaluations/{evaluation_id}", {
					params: { path: { evaluation_id: evaluationId } },
				}),
			),
		refetchInterval: (query) => {
			const status = query.state.data?.status;
			return status === "queued" || status === "running" ? POLL_MS : false;
		},
	});
