/**
 * Finding the brief's agent loop diagram in a test. The diagram is hidden from
 * screen readers (the round's step list says the same), so it is found by its
 * words and by being hidden, never by role.
 */

import { within } from "@testing-library/react";

export function loopDiagram(canvas: HTMLElement): HTMLElement {
	const planner = within(canvas).getByText("Planner", {
		selector: "[aria-hidden='true'] *",
	});
	const diagram = planner.closest("[aria-hidden='true']");
	if (!(diagram instanceof HTMLElement)) {
		throw new Error("No hidden loop diagram");
	}
	return diagram;
}

/** One agent in the diagram, or the Checks badge, by its name. */
export function loopAgent(canvas: HTMLElement, name: string): HTMLElement {
	const node = within(loopDiagram(canvas))
		.getByText(name)
		.closest("[data-node]");
	if (!(node instanceof HTMLElement)) {
		throw new Error(`No "${name}" in the loop`);
	}
	return node;
}
