import { chakra } from "@chakra-ui/react";

/** A button that reads as a link: for switching between ways of doing the same thing. */
export const LinkButton = chakra("button", {
	base: {
		alignSelf: "flex-start",
		p: "0",
		bg: "transparent",
		border: "0",
		color: "link",
		fontWeight: "medium",
		textAlign: "start",
		textDecoration: "underline",
		textUnderlineOffset: "3px",
		cursor: "pointer",
		_hover: { textDecorationThickness: "2px" },
	},
});

/** Inline text links, styled as LiGHT's. */
export const linkStyle = {
	color: "link",
	fontWeight: "medium",
	textDecoration: "underline",
	textUnderlineOffset: "3px",
	_hover: { textDecorationThickness: "2px" },
} as const;
