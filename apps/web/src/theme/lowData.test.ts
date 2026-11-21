/**
 * Low data mode (UI_UX_SPEC §13): the page's fonts become the system's, so no
 * font file is fetched, and nothing animates.
 */

import { describe, expect, it } from "vitest";
import { system } from "./system";

type Rules = Record<string, Record<string, string> | undefined>;

// Chakra nests each kind of style in a layer and writes each selector as "&…".
// Token variables must be overridden in their own layer: it comes after "base".
const globalRules =
	(system.getGlobalCss() as Record<string, Rules | undefined>)["@layer base"] ??
	{};
const tokenRules =
	(system.getTokenCss() as Record<string, Rules | undefined>)[
		"@layer tokens"
	] ?? {};

/** The declarations of the rule whose selector list includes `selector`. */
function ruleFor(
	selector: string,
	rules: Rules = globalRules,
): Record<string, string> {
	const found = Object.entries(rules).find(([key]) =>
		key
			.split(",")
			.some((part) => part.trim().replace(/^&|\s+&$/g, "") === selector),
	);
	if (found?.[1] === undefined) {
		throw new Error(`no global rule for ${selector}`);
	}
	return found[1];
}

describe("low data mode", () => {
	it("draws body text and headings in the system's fonts, never the bundled ones", () => {
		const fonts = ruleFor("html[data-low-data]", tokenRules);
		expect(system.token.var("fonts.body")).toBe("var(--ht-fonts-body)");
		expect(system.token.var("fonts.heading")).toBe("var(--ht-fonts-heading)");

		const body = fonts["--ht-fonts-body"] ?? "";
		const heading = fonts["--ht-fonts-heading"] ?? "";
		expect(body).toContain("system-ui");
		expect(heading).not.toBe("");
		expect(`${body} ${heading}`).not.toMatch(/Manrope|Brand Headline/);
	});

	it("stops every animation, as the reduced-motion switch does", () => {
		expect(ruleFor("html[data-low-data] *")).toBe(
			ruleFor("html[data-motion=reduced] *"),
		);
		expect(ruleFor("html[data-low-data] *")).toMatchObject({
			animationDuration: "0.01ms !important",
		});
		expect(
			ruleFor("html[data-low-data]::view-transition-group(*)"),
		).toMatchObject({ animation: "none !important" });
	});
});
