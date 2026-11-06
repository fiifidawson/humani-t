import { chakra } from "@chakra-ui/react";

/** Off-screen until focused, then the first thing on the page (WCAG 2.4.1). */
export const SkipLink = chakra("a", {
	base: {
		position: "absolute",
		left: "2",
		top: "-60px",
		zIndex: "100",
		bg: "bg.panel",
		color: "link",
		borderWidth: "1px",
		borderColor: "border.emphasized",
		rounded: "card",
		px: "3",
		py: "2",
		_focus: { top: "2" },
	},
});
