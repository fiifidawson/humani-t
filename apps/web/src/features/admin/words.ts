/**
 * The words the evaluation screens share: how far an evaluation has got, and the
 * model line (UI_UX_SPEC §8.6).
 */

import type { components } from "../../api/schema.gen";

export type EvaluationSummary = components["schemas"]["EvaluationSummaryOut"];

export function progressLine(evaluation: EvaluationSummary): string {
	switch (evaluation.status) {
		case "queued":
			return "Waiting to start";
		case "running":
			return `Answered ${evaluation.answered_count} of ${evaluation.question_count} questions`;
		case "finished":
			return "Finished";
		case "failed":
			return `Stopped: ${evaluation.error ?? "for an unknown reason."}`;
	}
}

export function modelLine(evaluation: EvaluationSummary): string {
	const judged = `Judged by ${evaluation.judge_model}.`;
	return evaluation.writer_model === null
		? judged
		: `Made by ${evaluation.writer_model}. ${judged}`;
}
