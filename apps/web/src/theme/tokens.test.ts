/**
 * Every screen must work in the light and the dark theme (UI_UX_SPEC §9.1).
 * That holds only while components read semantic tokens, which switch with
 * the theme; a raw colour stays the same in both and breaks one of them.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const sourceRoot = join(import.meta.dirname, "..");

// The QR code is the one exception (§9.1): phone cameras need black on white.
const EXCEPTIONS = new Set(["theme/system.ts", "features/auth/QrCode.tsx"]);

function componentFiles(directory: string): string[] {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) {
			return entry.name === "test" ? [] : componentFiles(path);
		}
		return /\.tsx?$/.test(entry.name) && !/\.(test|gen)\.tsx?$/.test(entry.name)
			? [path]
			: [];
	});
}

describe("colours come from the theme's tokens", () => {
	it("no component names a raw colour", () => {
		const offenders = componentFiles(sourceRoot)
			.map((path) => relative(sourceRoot, path).replaceAll("\\", "/"))
			.filter((path) => !EXCEPTIONS.has(path))
			.filter((path) =>
				/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/.test(
					readFileSync(join(sourceRoot, path), "utf8"),
				),
			);

		expect(offenders).toEqual([]);
	});
});
