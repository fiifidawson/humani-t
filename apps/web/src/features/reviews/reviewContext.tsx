/**
 * What every part of a review's page shares: its data, what the caller may do,
 * the composer, and the writes (UI_UX_SPEC §11.3). The chat cards and the
 * canvases read this instead of fetching or mutating on their own, so one
 * accept, from the card or from the canvas, is the same request and the same
 * pending state.
 *
 * The rules (who may accept, which version is current) are the API's: this
 * only hides actions the caller's role cannot take (§3.3) and sends the rest.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	createContext,
	type ReactNode,
	useCallback,
	useContext,
	useMemo,
} from "react";
import { unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { keys, projectQuery, sessionQuery } from "../../app/queries";
import { planQuery, reviewQuery, runsQuery } from "../../app/reviewQueries";
import type { Project } from "../projects/format";
import { PIPELINE, type StageId } from "../runs/runEvents";
import type { PlanMode } from "./format";

type Schemas = components["schemas"];
export type Review = Schemas["ReviewOut"];
export type Plan = Schemas["PlanOut"];
export type Version = Schemas["VersionOut"];
export type Run = Schemas["RunOut"];
export type Thread = Schemas["ThreadOut"];
// By position, not by name: the generated name carries the module path.
export type FollowUp = Thread["questions"][number];

export type AnswerInput =
	| { questionId: string; answer: string | null }
	| { draftNow: true };

export function useReviewActions(reviewId: string) {
	const api = useApi();
	const queryClient = useQueryClient();
	const path = { path: { review_id: reviewId } };

	const showPlan = (plan: Plan) => {
		queryClient.setQueryData(keys.plan(reviewId), plan);
		void queryClient.invalidateQueries({ queryKey: keys.review(reviewId) });
	};
	// 409 means the plan moved on under us (§11.4): reload it and show what changed.
	const reloadOnConflict = (error: unknown) => {
		if (isApiError(error, "conflict")) {
			void queryClient.invalidateQueries({ queryKey: keys.plan(reviewId) });
		}
	};
	const reloadRuns = () =>
		queryClient.invalidateQueries({ queryKey: keys.runs(reviewId) });

	const startPlan = useMutation({
		mutationFn: (mode: PlanMode) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/plan", {
					params: path,
					body: { mode },
				}),
			),
		onSuccess: showPlan,
		onError: reloadOnConflict,
	});

	const answer = useMutation({
		mutationFn: (input: AnswerInput) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/plan/answers", {
					params: path,
					body:
						"draftNow" in input
							? { answers: [], draft_now: true }
							: {
									answers: [
										{ question_id: input.questionId, answer: input.answer },
									],
									draft_now: false,
								},
				}),
			),
		onSuccess: showPlan,
		onError: reloadOnConflict,
	});

	const change = useMutation({
		mutationFn: (input: { request: string; basedOn: number }) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/plan/changes", {
					params: path,
					body: { request: input.request, based_on_version: input.basedOn },
				}),
			),
		onSuccess: showPlan,
		onError: reloadOnConflict,
	});

	const startRun = useMutation({
		mutationFn: (stages: readonly StageId[]) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/runs", {
					params: path,
					body: { stages: [...stages] },
				}),
			),
		onSuccess: reloadRuns,
		onError: (error) => {
			if (isApiError(error, "conflict")) {
				void reloadRuns();
			}
		},
	});

	const accept = useMutation({
		mutationFn: (version: number) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/plan/accept", {
					params: path,
					body: { version },
				}),
			),
		onSuccess: (plan) => {
			showPlan(plan);
			// "Accept and search" is one step for the person (§5.3.4): the
			// vocabulary is confirmed, then the run starts where the change matters.
			const stages = stagesFrom(plan.rerun_from);
			if (stages.length > 0) {
				startRun.mutate(stages);
			}
		},
		onError: reloadOnConflict,
	});

	const cancel = useMutation({
		mutationFn: (runId: string) =>
			unwrap(
				api.POST("/api/v1/runs/{run_id}/cancel", {
					params: { path: { run_id: runId } },
				}),
			),
		onSuccess: reloadRuns,
	});

	const decide = useMutation({
		mutationFn: (input: { gateId: string; decision: "approve" | "reject" }) =>
			unwrap(
				api.POST("/api/v1/gates/{gate_id}", {
					params: { path: { gate_id: input.gateId } },
					body: { decision: input.decision },
				}),
			),
	});

	const ask = useMutation({
		mutationFn: (input: AskInput) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/questions", {
					params: path,
					body: { question: input.question, thread_id: input.threadId },
				}),
			),
		// The question goes into its thread before the pending state ends, so
		// the person's turn never blinks out while the threads reload.
		onSuccess: (question) => {
			queryClient.setQueryData(
				keys.threads(reviewId),
				(threads: Thread[] | undefined) =>
					withQuestion(threads ?? [], question),
			);
			void queryClient.invalidateQueries({ queryKey: keys.threads(reviewId) });
		},
		onError: (error) => {
			if (isApiError(error, "conflict")) {
				void queryClient.invalidateQueries({
					queryKey: keys.threads(reviewId),
				});
			}
		},
	});

	return { startPlan, answer, change, accept, startRun, cancel, decide, ask };
}

export interface AskInput {
	question: string;
	/** The thread to continue; `null` starts one. */
	threadId: string | null;
}

/** `threads` with `question` added to its thread, or in a new thread first. */
export function withQuestion(
	threads: readonly Thread[],
	question: FollowUp,
): Thread[] {
	if (!threads.some((thread) => thread.id === question.thread_id)) {
		const thread: Thread = {
			id: question.thread_id,
			review_id: question.review_id,
			created_at: question.asked_at,
			created_by: question.asked_by,
			questions: [question],
		};
		return [thread, ...threads];
	}
	return threads.map((thread) =>
		thread.id !== question.thread_id
			? thread
			: {
					...thread,
					questions: [
						...thread.questions.filter((q) => q.id !== question.id),
						question,
					].sort((a, b) => a.position - b.position),
				},
	);
}

export type ReviewActions = ReturnType<typeof useReviewActions>;

export interface ReviewContextValue {
	project: Project;
	review: Review;
	plan: Plan;
	/** Newest first, as the API lists them. */
	runs: Run[];
	/** Owners and reviewers plan and run; viewers read (UI_UX_SPEC §3.3). */
	canManage: boolean;
	userId: string | null;
	actions: ReviewActions;
	setComposerText: (text: string) => void;
	focusComposer: () => void;
}

const COMPOSER_ID = "composer-input";

/**
 * Load what a review's pages share. `value` is null until everything has
 * loaded; `failed` is the first read that failed, if any.
 */
export function useReviewValue(
	projectId: string,
	reviewId: string,
	setComposerText: (text: string) => void,
) {
	const api = useApi();
	const project = useQuery(projectQuery(api, projectId));
	const review = useQuery(reviewQuery(api, projectId, reviewId));
	const plan = useQuery(planQuery(api, reviewId));
	const runs = useQuery(runsQuery(api, reviewId));
	const session = useQuery(sessionQuery(api));
	const actions = useReviewActions(reviewId);
	const focusComposer = useCallback(
		() => document.getElementById(COMPOSER_ID)?.focus(),
		[],
	);
	const value = useMemo<ReviewContextValue | null>(
		() =>
			project.data === undefined ||
			review.data === undefined ||
			plan.data === undefined ||
			runs.data === undefined
				? null
				: {
						project: project.data,
						review: review.data,
						plan: plan.data,
						runs: runs.data,
						canManage: project.data.role !== "viewer",
						userId: session.data?.user.id ?? null,
						actions,
						setComposerText,
						focusComposer,
					},
		[
			project.data,
			review.data,
			plan.data,
			runs.data,
			session.data,
			actions,
			setComposerText,
			focusComposer,
		],
	);
	const failed = [review, plan, runs].find((query) => query.isError);
	return { value, actions, failed };
}

const ReviewContext = createContext<ReviewContextValue | null>(null);

export function ReviewProvider({
	value,
	children,
}: {
	value: ReviewContextValue;
	children: ReactNode;
}) {
	return <ReviewContext value={value}>{children}</ReviewContext>;
}

export function useReview(): ReviewContextValue {
	const value = useContext(ReviewContext);
	if (value === null) {
		throw new Error("useReview must be used inside <ReviewProvider>");
	}
	return value;
}

/** A run's number as people count them: the first run is Run 1. */
export function useRunNumber(runId: string): number {
	const { runs } = useReview();
	return useMemo(() => {
		const index = runs.findIndex((run) => run.id === runId);
		return index < 0 ? runs.length + 1 : runs.length - index;
	}, [runs, runId]);
}

/** A run's requested stages in pipeline order, ignoring any the API may add later. */
/**
 * The stages a run of an accepted version needs (Wave 2, prompt-first): all of
 * them after a changed search, Screen onwards after changed criteria alone,
 * none when nothing changed. The API decides which (`rerun_from`).
 */
export function stagesFrom(rerunFrom: "queries" | "screen" | null): StageId[] {
	if (rerunFrom === null) {
		return [];
	}
	return PIPELINE.slice(PIPELINE.indexOf(rerunFrom));
}

export function stagesOf(run: Run): StageId[] {
	return PIPELINE.filter((stage) => run.stages.includes(stage));
}
