/**
 * The Chakra UI system: LiGHT's brand as tokens (UI_UX_SPEC §9).
 *
 * Raw brand colours are `colors.light.*`; components read only the semantic
 * tokens below, which say what a colour is for and switch with the theme. Every
 * text and UI pair they form is measured in UI_UX_SPEC §9.2. Light Blue and both
 * yellows fail as text on white or Eggshell, so on light backgrounds they appear only as
 * decoration, never as text or as the only sign of a state.
 *
 * Only the recipes the app uses are registered, which keeps Chakra's share of
 * the first load down; a component whose recipe is missing renders unstyled.
 */

import {
	createSystem,
	defaultBaseConfig,
	defineConfig,
} from "@chakra-ui/react";
import {
	alertSlotRecipe,
	animationStyles,
	badgeRecipe,
	breakpoints,
	buttonRecipe,
	checkboxSlotRecipe,
	codeRecipe,
	dialogSlotRecipe,
	headingRecipe,
	inputRecipe,
	keyframes,
	layerStyles,
	linkRecipe,
	radioGroupSlotRecipe,
	semanticTokens,
	separatorRecipe,
	skeletonRecipe,
	tableSlotRecipe,
	textStyles,
	tokens,
} from "@chakra-ui/react/theme";

/** LiGHT Brand Guidelines, August 2026. */
const BRAND = {
	darkBlue: "#0C43A0",
	lightBlue: "#68AFE7",
	darkYellow: "#EBA547",
	lightYellow: "#F6D54D",
	darkPurple: "#79123F",
	lightPurple: "#AE3156",
	eggshell: "#FEFFF7",
} as const;

/** Light and dark values for one semantic colour. */
function mode(light: string, dark: string) {
	return { value: { base: light, _dark: dark } };
}

/** A colour palette in the shape Chakra's recipes read through `colorPalette`. */
function palette(parts: {
	solid: [string, string];
	contrast: [string, string];
	fg: [string, string];
	subtle: [string, string];
	muted: [string, string];
	emphasized: [string, string];
	focusRing: [string, string];
}) {
	return Object.fromEntries(
		Object.entries(parts).map(([name, [light, dark]]) => [
			name,
			mode(light, dark),
		]),
	);
}

const config = defineConfig({
	cssVarsPrefix: "ht",
	// The reset Chakra's full theme carries (box-sizing, form controls inheriting the font).
	preflight: true,
	strictTokens: false,
	globalCss: {
		html: {
			colorPalette: "brand",
			color: "fg",
			bg: "bg",
			colorScheme: "light",
			"&.dark": { colorScheme: "dark" },
		},
		"html, body, #root": { height: "100%" },
		body: {
			margin: 0,
			fontFamily: "body",
			fontSize: "md",
			lineHeight: "1.6",
		},
		"h1, h2, h3": { textWrap: "balance" },
		"*::selection": { bg: "brand.muted" },
		":focus-visible": {
			outline: "2px solid",
			outlineColor: "focusRing",
			outlineOffset: "2px",
		},
		"[tabindex='-1']:focus:not(:focus-visible)": { outline: "none" },
		"*, *::before, *::after": {
			_motionReduce: {
				animationDuration: "0.01ms !important",
				animationIterationCount: "1 !important",
				transitionDuration: "0.01ms !important",
			},
		},
	},
	theme: {
		breakpoints,
		keyframes,
		layerStyles,
		textStyles,
		animationStyles,
		tokens: {
			...tokens,
			colors: {
				...tokens.colors,
				light: Object.fromEntries(
					Object.entries(BRAND).map(([name, value]) => [name, { value }]),
				),
			},
			fonts: {
				heading: {
					value: '"Brand Headline", Georgia, "Times New Roman", serif',
				},
				body: {
					value:
						'"Manrope Variable", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif',
				},
				mono: {
					value:
						'ui-monospace, "Cascadia Mono", "SFMono-Regular", Menlo, Consolas, monospace',
				},
			},
			radii: {
				...tokens.radii,
				card: { value: "8px" },
				pill: { value: "300px" },
			},
		},
		semanticTokens: {
			...semanticTokens,
			colors: {
				...semanticTokens.colors,
				bg: {
					// Pure white, the owner's choice over Eggshell for the page background.
					DEFAULT: mode("#FFFFFF", "#060E22"),
					panel: mode("#FFFFFF", "#0B1631"),
					subtle: mode("#EEF2F9", "#12204A"),
					muted: mode("#E6EAF5", "#1A2B5C"),
					emphasized: mode("#D9DEEA", "#24325A"),
					inverted: mode("#0A1633", BRAND.eggshell),
					error: mode("#F8E9EE", "#2A0E1A"),
					warning: mode("#FDF4DC", "#241D08"),
					info: mode("#E6EAF5", "#12204A"),
					success: mode("#E6EAF5", "#12204A"),
				},
				fg: {
					DEFAULT: mode("#0A1633", BRAND.eggshell),
					muted: mode("#4A5570", "#B7C0D6"),
					subtle: mode("#4A5570", "#B7C0D6"),
					inverted: mode(BRAND.eggshell, "#0A1633"),
					error: mode(BRAND.lightPurple, "#F08BA8"),
					warning: mode("#5C3D00", BRAND.lightYellow),
					info: mode(BRAND.darkBlue, BRAND.lightBlue),
					success: mode(BRAND.darkBlue, BRAND.lightBlue),
				},
				border: {
					DEFAULT: mode("#D9DEEA", "#24325A"),
					muted: mode("#D9DEEA", "#24325A"),
					subtle: mode("#E6EAF5", "#1A2B5C"),
					emphasized: mode("#7A8499", "#6A79A3"),
					inverted: mode("#0A1633", BRAND.eggshell),
					error: mode(BRAND.lightPurple, "#F08BA8"),
					warning: mode(BRAND.darkYellow, BRAND.darkYellow),
					info: mode(BRAND.darkBlue, BRAND.lightBlue),
					success: mode(BRAND.darkBlue, BRAND.lightBlue),
				},
				focusRing: mode(BRAND.darkBlue, BRAND.lightBlue),
				link: mode(BRAND.darkBlue, BRAND.lightBlue),
				/** Light Blue: decoration only on light backgrounds (2.37:1 on white). */
				accent: mode(BRAND.lightBlue, BRAND.lightBlue),
				marker: mode("#000000", BRAND.eggshell),
				scrim: mode("rgb(0 0 0 / 0.6)", "rgb(0 0 0 / 0.7)"),
				brand: palette({
					solid: [BRAND.darkBlue, BRAND.lightBlue],
					contrast: [BRAND.eggshell, "#06102A"],
					fg: [BRAND.darkBlue, BRAND.lightBlue],
					subtle: ["#E6EAF5", "#12204A"],
					muted: ["#D3DDF2", "#1A2B5C"],
					emphasized: ["#C2D0EC", "#24325A"],
					focusRing: [BRAND.darkBlue, BRAND.lightBlue],
				}),
				danger: palette({
					solid: [BRAND.lightPurple, "#F08BA8"],
					contrast: [BRAND.eggshell, "#06102A"],
					fg: [BRAND.lightPurple, "#F08BA8"],
					subtle: ["#F8E9EE", "#2A0E1A"],
					muted: ["#F1D3DD", "#3D1426"],
					emphasized: ["#E8BDCB", "#521B33"],
					focusRing: [BRAND.lightPurple, "#F08BA8"],
				}),
				// Yellow marks a caution with an icon or a bar, never the words: ink
				// text reads better, and yellow text fails contrast on white.
				caveat: palette({
					solid: [BRAND.darkYellow, BRAND.darkYellow],
					contrast: ["#0A1633", "#06102A"],
					fg: ["#0A1633", BRAND.eggshell],
					subtle: ["#FDF4DC", "#241D08"],
					muted: ["#FAE8B8", "#3A2E0C"],
					emphasized: ["#F6D54D", "#4D3D10"],
					focusRing: ["#5C3D00", BRAND.lightYellow],
				}),
				benefit: {
					fg: mode(BRAND.darkBlue, BRAND.lightBlue),
					bg: mode("#E6EAF5", "#12204A"),
					line: mode(BRAND.darkBlue, BRAND.lightBlue),
				},
				risk: {
					fg: mode("#0A1633", BRAND.eggshell),
					bg: mode("#FBEFDA", "#2A1E08"),
					line: mode(BRAND.darkYellow, BRAND.darkYellow),
				},
				masked: {
					fg: mode(BRAND.darkBlue, BRAND.lightBlue),
					bg: mode("#E8F1FB", "#0F2550"),
					line: mode(BRAND.lightBlue, BRAND.lightBlue),
				},
			},
		},
		recipes: {
			badge: badgeRecipe,
			// Pill-shaped, Manrope Medium, a 0.2 s transition: the buttons on LiGHT's site.
			button: {
				...buttonRecipe,
				base: {
					...buttonRecipe.base,
					borderRadius: "pill",
					fontWeight: "medium",
					transition:
						"background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease",
				},
			},
			code: codeRecipe,
			heading: headingRecipe,
			// An input's edge identifies it, so it needs 3:1 (WCAG 1.4.11); the hairline is 1.3:1.
			input: {
				...inputRecipe,
				base: { ...inputRecipe.base, borderRadius: "card" },
				variants: {
					...inputRecipe.variants,
					variant: {
						...inputRecipe.variants?.variant,
						outline: {
							...inputRecipe.variants?.variant?.outline,
							borderColor: "border.emphasized",
						},
					},
				},
			},
			link: linkRecipe,
			separator: separatorRecipe,
			skeleton: skeletonRecipe,
		},
		slotRecipes: {
			alert: alertSlotRecipe,
			checkbox: checkboxSlotRecipe,
			dialog: dialogSlotRecipe,
			radioGroup: radioGroupSlotRecipe,
			table: tableSlotRecipe,
		},
	},
});

export const system = createSystem(defaultBaseConfig, config);
