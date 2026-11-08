/**
 * Whether the open canvas takes the whole workspace (UI_UX_SPEC §4.1 "Full
 * width"). The review page owns the state; the canvas's header toggles it.
 */

import { createContext, useContext } from "react";

export interface CanvasWidth {
	isFullWidth: boolean;
	setFullWidth: (isFullWidth: boolean) => void;
}

const CanvasWidthContext = createContext<CanvasWidth>({
	isFullWidth: false,
	setFullWidth: () => {},
});

export const CanvasWidthProvider = CanvasWidthContext.Provider;

export function useCanvasWidth(): CanvasWidth {
	return useContext(CanvasWidthContext);
}
