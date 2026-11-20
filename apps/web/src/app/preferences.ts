/**
 * Per-browser display preferences: theme, reduced motion, the folded rail, the
 * rail's open collections and panel widths.
 *
 * Only these live in localStorage. They say nothing about a project or a
 * person's work; question text never goes there (UI_UX_SPEC §13.2).
 */

import { useCallback, useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const THEME_KEY = "humani-t:theme";
const RAIL_KEY = "humani-t:rail-folded";
const MOTION_KEY = "humani-t:reduce-motion";
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

export function readReducedMotion(): boolean {
	return read(MOTION_KEY) === "true";
}

/** The system's own reduced-motion setting applies regardless (theme/system.ts). */
export function applyReducedMotion(isReduced: boolean): void {
	const root = document.documentElement;
	if (isReduced) {
		root.setAttribute("data-motion", "reduced");
	} else {
		root.removeAttribute("data-motion");
	}
}

export function useReducedMotion(): [boolean, (isReduced: boolean) => void] {
	const isReduced = useSyncExternalStore(subscribe, readReducedMotion);
	const setReduced = useCallback((value: boolean) => {
		applyReducedMotion(value);
		write(MOTION_KEY, value ? "true" : null);
	}, []);
	return [isReduced, setReduced];
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

const OPEN_COLLECTIONS_KEY = "humani-t:open-collections";

function readOpenCollections(): string {
	return read(OPEN_COLLECTIONS_KEY) ?? "[]";
}

/** Which of the rail's collections are unfolded: their ids, which name nothing on their own. */
export function useOpenCollections(): [
	ReadonlySet<string>,
	(collectionId: string, isOpen: boolean) => void,
] {
	const stored = useSyncExternalStore(subscribe, readOpenCollections);
	const open = parseIds(stored);
	const setOpen = useCallback((collectionId: string, isOpen: boolean) => {
		const next = parseIds(readOpenCollections());
		if (isOpen) {
			next.add(collectionId);
		} else {
			next.delete(collectionId);
		}
		write(
			OPEN_COLLECTIONS_KEY,
			next.size === 0 ? null : JSON.stringify([...next]),
		);
	}, []);
	return [open, setOpen];
}

function parseIds(stored: string): Set<string> {
	try {
		const value: unknown = JSON.parse(stored);
		return new Set(
			Array.isArray(value)
				? value.filter((id): id is string => typeof id === "string")
				: [],
		);
	} catch {
		return new Set();
	}
}

export type PanelName = "chat" | "source";

export interface PanelLimits {
	initial: number;
	min: number;
	max: number;
}

/**
 * Widths in the zoomed page's CSS pixels (COMPACT_ZOOM): the chat's 640 shows
 * as 480 on screen. The most it can take still leaves the canvas a readable column.
 */
export const PANEL_LIMITS: Readonly<Record<PanelName, PanelLimits>> = {
	chat: { initial: 640, min: 440, max: 960 },
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
