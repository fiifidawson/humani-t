/**
 * Whether a CSS media query matches, following changes. For deciding what to
 * render; for styling alone, Chakra's responsive props are enough.
 */

import { useCallback, useSyncExternalStore } from "react";

/** Chakra's `md` and `lg` breakpoints, as the theme uses them. */
export const MEDIUM_SCREEN = "(min-width: 768px)";
export const WIDE_SCREEN = "(min-width: 1024px)";

const hasMatchMedia = () => typeof window.matchMedia === "function";

/** False where the browser cannot say (no `matchMedia`). */
export function useMediaQuery(query: string): boolean {
	const subscribe = useCallback(
		(onChange: () => void) => {
			if (!hasMatchMedia()) {
				return () => {};
			}
			const list = window.matchMedia(query);
			list.addEventListener("change", onChange);
			return () => list.removeEventListener("change", onChange);
		},
		[query],
	);
	return useSyncExternalStore(
		subscribe,
		() => hasMatchMedia() && window.matchMedia(query).matches,
	);
}
