/**
 * The one control for showing and hiding a section: a quiet button whose
 * arrow points right when the section is closed and down when it is open.
 */

import { Button } from "@chakra-ui/react";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

export function DisclosureButton({
	isOpen,
	controls,
	onToggle,
	children,
}: {
	isOpen: boolean;
	/** The id of the section it shows and hides. */
	controls: string;
	onToggle: () => void;
	children: ReactNode;
}) {
	return (
		<Button
			type="button"
			variant="ghost"
			size="sm"
			px="2"
			color="fg"
			aria-expanded={isOpen}
			aria-controls={controls}
			onClick={onToggle}
			css={{
				"& svg": {
					transition: "transform 0.15s ease",
					transform: isOpen ? "rotate(90deg)" : "none",
				},
			}}
		>
			<ChevronRight />
			{children}
		</Button>
	);
}
