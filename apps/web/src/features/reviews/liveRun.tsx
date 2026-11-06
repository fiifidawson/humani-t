/**
 * One stream for the review's latest run, shared by everything that shows it:
 * the run strip, the gate and screening cards, and the run canvas (UI_UX_SPEC
 * §11.2 "one SSE stream per run"). Opening the canvas must not open a second
 * connection, or the two views could disagree.
 */

import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect } from "react";
import { keys } from "../../app/queries";
import type { RunState } from "../runs/runEvents";
import { type Connection, useRunEvents } from "../runs/useRunEvents";
import type { Run } from "./reviewContext";

export interface LiveRun {
	run: Run;
	state: RunState;
	connection: Connection;
}

const LiveRunContext = createContext<LiveRun | null>(null);

export const runEventsUrl = (runId: string) => `/api/v1/runs/${runId}/events`;

/** Follow `run` for the children; give the caller's key the run id, so a new run starts clean. */
export function LiveRunProvider({
	run,
	children,
}: {
	run: Run;
	children: ReactNode;
}) {
	const { state, connection } = useRunEvents(runEventsUrl(run.id));
	const queryClient = useQueryClient();

	useEffect(() => {
		if (state.isFinished) {
			// The run's final state and the review's status are the API's to say.
			void queryClient.invalidateQueries({
				queryKey: keys.runs(run.review_id),
			});
			void queryClient.invalidateQueries({
				queryKey: keys.review(run.review_id),
			});
		}
	}, [state.isFinished, queryClient, run.review_id]);

	return (
		<LiveRunContext value={{ run, state, connection }}>
			{children}
		</LiveRunContext>
	);
}

/** The latest run as it happens, or `null` when the review has no run yet. */
export function useLiveRun(): LiveRun | null {
	return useContext(LiveRunContext);
}
