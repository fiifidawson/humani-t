/**
 * The reads of a review's screens, as TanStack Query options. Apart from
 * queries.ts so that only the lazily loaded review chunks carry them; their
 * keys stay in `keys` with every other key, so invalidation still has one home.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";
import { keys, type PaperFilter } from "./queries";

export const reviewsQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: keys.reviews(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/reviews", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export const reviewQuery = (
	api: ApiClient,
	projectId: string,
	reviewId: string,
) =>
	queryOptions({
		queryKey: keys.review(reviewId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/reviews/{review_id}", {
					params: { path: { project_id: projectId, review_id: reviewId } },
				}),
			),
	});

export const planQuery = (api: ApiClient, reviewId: string) =>
	queryOptions({
		queryKey: keys.plan(reviewId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/plan", {
					params: { path: { review_id: reviewId } },
				}),
			),
		// A change another member asked for is drafted by the model; look again
		// until it lands rather than show a stale draft as the latest.
		refetchInterval: (query) =>
			query.state.data?.pending_change == null ? false : 5000,
	});

/** A version never changes once written (ADR 0005), so it is never refetched. */
export const planVersionQuery = (
	api: ApiClient,
	reviewId: string,
	version: number,
) =>
	queryOptions({
		queryKey: keys.planVersion(reviewId, version),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/plan/versions/{version}", {
					params: { path: { review_id: reviewId, version } },
				}),
			),
		staleTime: Number.POSITIVE_INFINITY,
	});

export const runsQuery = (api: ApiClient, reviewId: string) =>
	queryOptions({
		queryKey: keys.runs(reviewId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/runs", {
					params: { path: { review_id: reviewId } },
				}),
			),
	});

export const papersQuery = (
	api: ApiClient,
	reviewId: string,
	filter: PaperFilter,
) =>
	queryOptions({
		queryKey: keys.papers(reviewId, filter),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/papers", {
					params: {
						path: { review_id: reviewId },
						query: {
							run_id: filter.runId,
							page: filter.page,
							...(filter.label === null ? {} : { label: filter.label }),
							...(filter.tier === null ? {} : { tier: filter.tier }),
						},
					},
				}),
			),
	});

export const paperQuery = (
	api: ApiClient,
	reviewId: string,
	paperId: string,
	runId: string,
) =>
	queryOptions({
		queryKey: keys.paper(reviewId, paperId, runId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/papers/{paper_id}", {
					params: {
						path: { review_id: reviewId, paper_id: paperId },
						query: { run_id: runId },
					},
				}),
			),
	});

export const prismaQuery = (api: ApiClient, reviewId: string, runId: string) =>
	queryOptions({
		queryKey: keys.prisma(reviewId, runId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/prisma", {
					params: { path: { review_id: reviewId }, query: { run_id: runId } },
				}),
			),
	});

/** A review's follow-up threads, newest first, each with its questions in order. */
export const threadsQuery = (api: ApiClient, reviewId: string) =>
	queryOptions({
		queryKey: keys.threads(reviewId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/reviews/{review_id}/threads", {
					params: { path: { review_id: reviewId } },
				}),
			),
	});

/** One thread; its page reads this for the thread's review, then the review's threads. */
export const threadQuery = (api: ApiClient, threadId: string) =>
	queryOptions({
		queryKey: keys.thread(threadId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/threads/{thread_id}", {
					params: { path: { thread_id: threadId } },
				}),
			),
	});

const QUESTION_POLL_MS = 2000;

/** One question, polled until it is answered or failed (docs/api.md "Follow-up questions"). */
export const questionQuery = (api: ApiClient, questionId: string) =>
	queryOptions({
		queryKey: keys.question(questionId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/questions/{question_id}", {
					params: { path: { question_id: questionId } },
				}),
			),
		refetchInterval: (query) => {
			const status = query.state.data?.status;
			return status === "answered" || status === "failed"
				? false
				: QUESTION_POLL_MS;
		},
	});
