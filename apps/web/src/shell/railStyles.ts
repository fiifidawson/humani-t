/**
 * The rail's look, after the navigation on LiGHT's site: Manrope at 16 px, ink
 * text, a pale blue fill on hover, and the current page in Dark Blue, semibold.
 * Breakpoints are UI_UX_SPEC §4.2: from 1280 px everything; 1024 to 1279 px
 * folded to icons; below 1024 px a drawer behind the menu button.
 */

import type { SystemStyleObject } from "@chakra-ui/react";

export const HEADER_HEIGHT = "64px";

/**
 * The signed-in app is drawn at 75%, the owner's chosen density; sign-in and
 * enrolment pages, outside AppShell, stay full size. Lengths inside it are in
 * the zoomed page's pixels: the rail's 240px shows as 180px.
 */
export const COMPACT_ZOOM = 0.75;

const visuallyHidden: SystemStyleObject = {
	position: "absolute",
	width: "1px",
	height: "1px",
	overflow: "hidden",
	clip: "rect(0 0 0 0)",
	whiteSpace: "nowrap",
};

const iconOnly: SystemStyleObject = {
	"& .rail-label": visuallyHidden,
	"& .rail-item": { justifyContent: "center", px: "0" },
	"& .rail-section": { display: "none" },
};

export const railItem: SystemStyleObject = {
	display: "flex",
	alignItems: "center",
	gap: "3",
	w: "full",
	minH: "10",
	px: "3",
	py: "2",
	rounded: "card",
	fontSize: "md",
	lineHeight: "1.4",
	color: "fg",
	textAlign: "start",
	textDecoration: "none",
	bg: "transparent",
	cursor: "pointer",
	transition: "background-color 0.2s ease, color 0.2s ease",
	_hover: { bg: "bg.muted" },
	"&[aria-current=page]": {
		bg: "bg.muted",
		color: "link",
		fontWeight: "semibold",
	},
	"& svg": { flex: "none" },
};

export const rail: SystemStyleObject = {
	flex: "none",
	display: "flex",
	flexDirection: "column",
	gap: "1",
	w: "240px",
	px: "3",
	py: "4",
	overflowY: "auto",
	borderRightWidth: "1px",
	borderColor: "border",
	bg: "bg",
	"&.is-folded": { w: "72px", ...iconOnly },
	"@media (min-width: 1024px) and (max-width: 1279px)": {
		w: "72px",
		...iconOnly,
		"& .rail-fold": { display: "none" },
	},
	"@media (max-width: 1023px)": {
		position: "fixed",
		insetBlock: "0",
		left: "0",
		zIndex: "40",
		h: "auto",
		w: "280px",
		bg: "bg.panel",
		boxShadow: "lg",
		transform: "translateX(-105%)",
		visibility: "hidden",
		transition: "transform 0.2s ease, visibility 0.2s",
		"&.is-folded": { w: "280px" },
		"&.is-folded .rail-label": {
			position: "static",
			width: "auto",
			height: "auto",
			clip: "auto",
		},
		"&.is-folded .rail-item": { justifyContent: "flex-start", px: "3" },
		"&.is-folded .rail-section": { display: "block" },
		"&.is-open": { transform: "none", visibility: "visible" },
		"& .rail-fold": { display: "none" },
	},
};
