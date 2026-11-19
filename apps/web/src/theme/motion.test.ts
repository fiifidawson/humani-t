/**
 * Motion (UI_UX_SPEC §9.5): short, never bouncing, and gone when the person
 * asks for less, from their computer or from Account.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { system } from "./system";

const sourceRoot = join(import.meta.dirname, "..");

function componentFiles(directory: string): string[] {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) {
			return entry.name === "test" || entry.name === "theme"
				? []
				: componentFiles(path);
		}
		return /.tsx?$/.test(entry.name) && !/.(test|gen).tsx?$/.test(entry.name)
			? [path]
			: [];
	});
}

const globalCss = JSON.stringify(system.getGlobalCss());
const resolved = (styles: Parameters<typeof system.css>[0]) =>
	JSON.stringify(system.css(styles));

describe("motion", () => {
	it("cards rise in with a fade and a 6 px rise, over 350 ms", () => {
		expect(globalCss).toContain('"@keyframes rise-in"');
		expect(globalCss).toContain("translateY(6px)");
		const rise = resolved({ animationStyle: "rise-in" });
		expect(rise).toContain("rise-in");
		expect(rise).toContain("350ms");
	});

	it("an opened section slides open over 200 ms", () => {
		expect(globalCss).toContain('"@keyframes reveal"');
		const reveal = resolved({ animationStyle: "reveal" });
		expect(reveal).toContain("reveal");
		expect(reveal).toContain("200ms");
	});

	it("pages cross-fade over 200 ms", () => {
		expect(globalCss).toContain("::view-transition-old(root)");
		expect(globalCss).toContain("0.2s");
	});

	it("buttons press in slightly, without bouncing", () => {
		const button = JSON.stringify(system.getRecipe("button"));
		expect(button).toContain("scale(0.98)");
		expect(globalCss).not.toMatch(/cubic-bezier\([^)]*1\.[1-9]/);
	});

	// Chakra reads "@keyframes" inside a component's css as a condition, so the
	// animation never exists: the run's spinner and the reply dots stood still.
	it("defines every animation's keyframes in the theme, never in a component", () => {
		const inComponents = componentFiles(sourceRoot).filter((path) =>
			readFileSync(path, "utf8").includes("@keyframes"),
		);
		expect(inComponents).toEqual([]);
		expect(globalCss).toContain('"@keyframes reply-dot"');
		expect(globalCss).toContain('"@keyframes spin"');
	});

	it("stops every animation when Account asks for reduced motion, not only the system", () => {
		expect(globalCss).toContain("html[data-motion=reduced]");
		expect(globalCss).toContain("::view-transition-group(*)");
	});
});
