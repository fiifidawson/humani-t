import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

interface ShortcutActions {
	toggleRail: () => void;
	closeDrawer: () => void;
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
 * The single-key shortcuts of UI_UX_SPEC §11.5 that P1 needs: `[` folds the
 * rail, `g h` and `g p` go home or to projects, `/` focuses the composer, Esc
 * closes the drawer. Off while typing (WCAG 2.1.4).
 */
export function useShortcuts({
	toggleRail,
	closeDrawer,
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
				event.defaultPrevented ||
				event.ctrlKey ||
				event.metaKey ||
				event.altKey ||
				isTyping(event.target)
			) {
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
	}, [navigate, toggleRail, closeDrawer]);
}
