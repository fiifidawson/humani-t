/**
 * Per-browser display preferences: theme, the folded rail and panel widths.
 *
 * Only these live in localStorage. They say nothing about a project or a
 * person's work; question text never goes there (UI_UX_SPEC §13.2).
 */

import { useCallback, useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const THEME_KEY = "humani-t:theme";
const RAIL_KEY = "humani-t:rail-folded";
const listeners = new Set<() => void>();
// Where a preference lives when storage refuses it: for this page view only.
const unsaved = new Map<string, string | null>();

// Storage can throw: blocked cookies, private windows, a full quota. A
// preference that cannot be saved still applies until the page is closed.
function read(key: string): string | null {
	try {
		return window.localStorage.getItem(key);
	} catch {
		return unsaved.get(key) ?? null;
	}
}

function write(key: string, value: string | null): void {
	unsaved.set(key, value);
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

export type PanelName = "chat" | "source";

export interface PanelLimits {
	initial: number;
	min: number;
	max: number;
}

/**
 * Widths in CSS pixels. The chat's are as drawn, after its 75% zoom; the most
 * it can take still leaves the canvas room for a readable column.
 */
export const PANEL_LIMITS: Readonly<Record<PanelName, PanelLimits>> = {
	chat: { initial: 480, min: 320, max: 720 },
	source: { initial: 400, min: 320, max: 640 },
};

export function clampWidth(width: number, limits: PanelLimits): number {
	return Math.min(limits.max, Math.max(limits.min, Math.round(width)));
}

/** A panel's width, and a setter that keeps it within its limits; null resets it. */
export function usePanelWidth(
	name: PanelName,
): [number, (width: number | null) => void] {
	const key = `humani-t:width:${name}`;
	const limits = PANEL_LIMITS[name];
	const stored = useSyncExternalStore(subscribe, () => read(key));
	const parsed = stored === null ? Number.NaN : Number(stored);
	const width = Number.isFinite(parsed)
		? clampWidth(parsed, limits)
		: limits.initial;
	const setWidth = useCallback(
		(value: number | null) =>
			write(key, value === null ? null : String(clampWidth(value, limits))),
		[key, limits],
	);
	return [width, setWidth];
}
