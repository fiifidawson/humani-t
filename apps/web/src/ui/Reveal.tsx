/**
 * What a disclosure shows, sliding open as it appears (UI_UX_SPEC §9.5).
 *
 * Closing is immediate: content on its way out should get out of the way, and
 * keeping it mounted to animate would leave screen readers a closed section to find.
 */

import { Box } from "@chakra-ui/react";
import type { ReactNode } from "react";

export function Reveal({ children }: { children: ReactNode }) {
	return (
		<Box display="grid" animationStyle="reveal">
			<Box minW="0" minH="0" animationStyle="reveal-clip">
				{children}
			</Box>
		</Box>
	);
}
