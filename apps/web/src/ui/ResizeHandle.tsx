/**
 * A handle on a panel's edge for changing its width (the WAI-ARIA window
 * splitter): the pointer turns to ↔ over it, dragging or the arrow keys move
 * it within the panel's limits, and a double click puts the width back. The
 * width itself belongs to the caller; this only reports a new one.
 */

import { chakra, type HTMLChakraProps } from "@chakra-ui/react";
import {
	type KeyboardEvent,
	type PointerEvent,
	useEffect,
	useRef,
	useState,
} from "react";
import { clampWidth, type PanelLimits } from "../app/preferences";

const KEY_STEP = 16;

/** The CSS zoom in effect on an element; 1 where the browser cannot say. */
function zoomOf(element: Element): number {
	const zoom: unknown = Reflect.get(element, "currentCSSZoom");
	return typeof zoom === "number" && zoom > 0 ? zoom : 1;
}

const Handle = chakra("div", {
	base: {
		position: "relative",
		flex: "none",
		w: "2",
		cursor: "col-resize",
		touchAction: "none",
		zIndex: "1",
		_before: {
			content: '""',
			position: "absolute",
			insetBlock: "0",
			left: "50%",
			w: "1px",
			transform: "translateX(-50%)",
			bg: "border",
			transition: "width 0.15s ease, background-color 0.15s ease",
		},
		"&:hover::before, &:focus-visible::before, &[data-dragging]::before": {
			w: "3px",
			bg: "brand.solid",
		},
	},
});

interface ResizeHandleProps
	extends Omit<HTMLChakraProps<"div">, "onResize" | "children"> {
	label: string;
	/** The id of the panel this resizes. */
	controls: string;
	/**
	 * The panel's edge the handle sits on. Moving away from the panel widens
	 * it: right from its end edge, left from its start edge.
	 */
	edge: "start" | "end";
	width: number;
	limits: PanelLimits;
	onResize: (width: number | null) => void;
}

export function ResizeHandle({
	label,
	controls,
	edge,
	width,
	limits,
	onResize,
	...rest
}: ResizeHandleProps) {
	const [isDragging, setDragging] = useState(false);
	const stopDragging = useRef<(() => void) | null>(null);
	const outwards = edge === "end" ? 1 : -1;

	useEffect(() => () => stopDragging.current?.(), []);

	const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
		if (event.button !== 0) {
			return;
		}
		// Stops the drag selecting the text on either side.
		event.preventDefault();
		const startX = event.clientX;
		const startWidth = width;
		// The pointer moves in screen pixels; the width is in the zoomed page's.
		const scale = zoomOf(event.currentTarget);
		const move = (moved: globalThis.PointerEvent) =>
			onResize(
				clampWidth(
					startWidth + (outwards * (moved.clientX - startX)) / scale,
					limits,
				),
			);
		const stop = () => {
			window.removeEventListener("pointermove", move);
			window.removeEventListener("pointerup", stop);
			window.removeEventListener("pointercancel", stop);
			stopDragging.current = null;
			setDragging(false);
		};
		// On the window, so the drag keeps up when the pointer outruns the handle.
		window.addEventListener("pointermove", move);
		window.addEventListener("pointerup", stop);
		window.addEventListener("pointercancel", stop);
		stopDragging.current = stop;
		setDragging(true);
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		const next: Record<string, number> = {
			ArrowRight: width + outwards * KEY_STEP,
			ArrowLeft: width - outwards * KEY_STEP,
			Home: limits.min,
			End: limits.max,
		};
		const target = next[event.key];
		if (target !== undefined) {
			event.preventDefault();
			onResize(clampWidth(target, limits));
		}
	};

	return (
		<Handle
			role="separator"
			aria-orientation="vertical"
			aria-label={label}
			aria-controls={controls}
			aria-valuenow={width}
			aria-valuemin={limits.min}
			aria-valuemax={limits.max}
			tabIndex={0}
			data-dragging={isDragging ? "" : undefined}
			onPointerDown={onPointerDown}
			onKeyDown={onKeyDown}
			onDoubleClick={() => onResize(null)}
			{...rest}
		/>
	);
}
