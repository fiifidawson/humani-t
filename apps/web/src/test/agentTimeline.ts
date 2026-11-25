/**
 * Reading the brief's agent timeline in a test: each round is an ordered list
 * named by its heading, one entry per step.
 */

import { within } from "@testing-library/react";

/** The entries of one round, by the round's name ("Round 1"). */
export function roundEntries(
	canvas: HTMLElement,
	round: string,
): HTMLElement[] {
	return within(within(canvas).getByRole("list", { name: round })).getAllByRole(
		"listitem",
	);
}

/** Whole seconds or minutes in a text: "41 s", "2 min". */
export const anyTime = /(^|\D)\d+ (s|min)\b/;
