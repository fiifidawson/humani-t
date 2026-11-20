import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { readSingleKeyShortcuts } from "../app/preferences";

interface ShortcutActions {
	toggleRail: () => void;
	closeDrawer: () => void;
	openPalette: () => void;
	openShortcuts: () => void;
}

const SEQUENCE_WINDOW_MS = 1000;

/** True when a key press belongs to whatever the person is typing in. */
function isTyping(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) {
		return false;
	}
	return (
		target.isContentEditable ||
		["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
	);
}

/**
 * Whether a key press may act as a single-key shortcut: no modifier, not typing,
 * not inside a dialog, and not turned off in Account (WCAG 2.1.4).
 */
export function allowsSingleKey(event: KeyboardEvent): boolean {
	return (
		!event.defaultPrevented &&
		!event.ctrlKey &&
		!event.metaKey &&
		!event.altKey &&
		!isTyping(event.target) &&
		!(
			event.target instanceof Element &&
			event.target.closest("[role=dialog]") !== null
		) &&
		readSingleKeyShortcuts()
	);
}

/**
 * The app-wide shortcuts of UI_UX_SPEC §11.5. Ctrl K or ⌘K opens search from
 * anywhere, a text field included. The single keys: `[` folds the rail, `g h`
 * and `g p` go home or to projects, `/` focuses the composer, `?` lists the
 * shortcuts. Esc closes the drawer. The review page adds `]`, `j` and `k`.
 */
export function useShortcuts({
	toggleRail,
	closeDrawer,
	openPalette,
	openShortcuts,
}: ShortcutActions): void {
	const navigate = useNavigate();
	const pendingG = useRef<number | null>(null);

	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				closeDrawer();
				return;
			}
			if (
				(event.ctrlKey || event.metaKey) &&
				!event.altKey &&
				event.key.toLowerCase() === "k"
			) {
				event.preventDefault();
				openPalette();
				return;
			}
			if (!allowsSingleKey(event)) {
				return;
			}
			const followsG =
				pendingG.current !== null &&
				Date.now() - pendingG.current < SEQUENCE_WINDOW_MS;
			pendingG.current = null;
			if (followsG && event.key === "h") {
				void navigate({ to: "/" });
			} else if (followsG && event.key === "p") {
				void navigate({ to: "/projects" });
			} else if (event.key === "g") {
				pendingG.current = Date.now();
			} else if (event.key === "[") {
				event.preventDefault();
				toggleRail();
			} else if (event.key === "?") {
				event.preventDefault();
				openShortcuts();
			} else if (event.key === "/") {
				const composer = document.getElementById("composer-input");
				if (composer !== null) {
					event.preventDefault();
					composer.focus();
				}
			}
		};
		document.addEventListener("keydown", onKeyDown);
		return () => document.removeEventListener("keydown", onKeyDown);
	}, [navigate, toggleRail, closeDrawer, openPalette, openShortcuts]);
}
