/**
 * jsdom has no screen and no `matchMedia`. This gives it one of a set width,
 * answering `min-width` and `max-width` queries in pixels and nothing else.
 */

import { vi } from "vitest";

export function setScreenWidth(width: number): void {
	vi.stubGlobal("matchMedia", (query: string) => {
		const min = /min-width:\s*(\d+)px/.exec(query)?.[1];
		const max = /max-width:\s*(\d+)px/.exec(query)?.[1];
		const matches =
			(min !== undefined || max !== undefined) &&
			(min === undefined || width >= Number(min)) &&
			(max === undefined || width <= Number(max));
		return {
			matches,
			media: query,
			onchange: null,
			addEventListener: () => {},
			removeEventListener: () => {},
			addListener: () => {},
			removeListener: () => {},
			dispatchEvent: () => false,
		};
	});
}
