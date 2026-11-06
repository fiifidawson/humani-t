/**
 * Per-browser display preferences: theme and the folded rail.
 *
 * Only these live in localStorage. They say nothing about a project or a
 * person's work; question text never goes there (UI_UX_SPEC §13.2).
 */

import { useCallback, useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const THEME_KEY = "humani-t:theme";
const RAIL_KEY = "humani-t:rail-folded";
const listeners = new Set<() => void>();

// Storage can throw: blocked cookies, private windows, a full quota. A
// preference that cannot be saved falls back to the default.
function read(key: string): string | null {
	try {
		return window.localStorage.getItem(key);
	} catch {
		return null;
	}
}

function write(key: string, value: string | null): void {
	try {
		if (value === null) {
			window.localStorage.removeItem(key);
		} else {
			window.localStorage.setItem(key, value);
		}
	} catch {
		// Kept for this page view only; see read().
	}
	for (const listener of listeners) {
		listener();
	}
}

function subscribe(listener: () => void): () => void {
	listeners.add(listener);
	return () => listeners.delete(listener);
}

export function readTheme(): ThemeChoice {
	const stored = read(THEME_KEY);
	return stored === "light" || stored === "dark" ? stored : "system";
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemPrefersDark(): boolean {
	return (
		typeof window.matchMedia === "function" &&
		window.matchMedia(DARK_QUERY).matches
	);
}

/**
 * Show `choice` on <html>: `data-theme` records the person's choice ("system"
 * removes it), and the `dark` or `light` class is the theme Chakra applies.
 */
export function applyTheme(choice: ThemeChoice): void {
	const root = document.documentElement;
	if (choice === "system") {
		root.removeAttribute("data-theme");
	} else {
		root.setAttribute("data-theme", choice);
	}
	const isDark =
		choice === "dark" || (choice === "system" && systemPrefersDark());
	root.classList.toggle("dark", isDark);
	root.classList.toggle("light", !isDark);
}

/** Re-apply the theme when the system's changes, for as long as the page is open. */
export function followSystemTheme(): void {
	if (typeof window.matchMedia === "function") {
		window
			.matchMedia(DARK_QUERY)
			.addEventListener("change", () => applyTheme(readTheme()));
	}
}

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
	const theme = useSyncExternalStore(subscribe, readTheme);
	const setTheme = useCallback((choice: ThemeChoice) => {
		applyTheme(choice);
		write(THEME_KEY, choice === "system" ? null : choice);
	}, []);
	return [theme, setTheme];
}

export function useRailFolded(): [boolean, (isFolded: boolean) => void] {
	const isFolded = useSyncExternalStore(
		subscribe,
		() => read(RAIL_KEY) === "true",
	);
	const setFolded = useCallback(
		(value: boolean) => write(RAIL_KEY, value ? "true" : null),
		[],
	);
	return [isFolded, setFolded];
}
