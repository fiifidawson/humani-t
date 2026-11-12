/**
 * The system registers only the recipes the app uses (system.ts), and a
 * component whose recipe is missing renders unstyled, which no behaviour test
 * notices. So every Chakra component the app imports must find its recipe here.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createSystem, defaultConfig } from "@chakra-ui/react";
import { describe, expect, it } from "vitest";
import { system } from "./system";

const sourceRoot = join(import.meta.dirname, "..");
const chakra = createSystem(defaultConfig);

function sourceFiles(directory: string): string[] {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) {
			return sourceFiles(path);
		}
		return /\.tsx?$/.test(entry.name) && !/\.(test|gen)\.tsx?$/.test(entry.name)
			? [path]
			: [];
	});
}

function chakraImports(source: string): string[] {
	return [
		...source.matchAll(/import\s*\{([^}]*)\}\s*from\s*"@chakra-ui\/react"/g),
	].flatMap((match) =>
		(match[1] ?? "")
			.split(",")
			.map((name) => name.replace(/^\s*type\s+/, "").trim())
			.filter((name) => /^[A-Z]/.test(name)),
	);
}

const recipeKey = (component: string) =>
	component.charAt(0).toLowerCase() + component.slice(1);

describe("theme recipes", () => {
	it("registers the recipe of every Chakra component the app uses", () => {
		const used = new Set(
			sourceFiles(sourceRoot).flatMap((path) =>
				chakraImports(readFileSync(path, "utf8")),
			),
		);

		const missing = [...used].map(recipeKey).filter((key) => {
			const hasOne = chakra.isRecipe(key) || chakra.isSlotRecipe(key);
			return hasOne && !system.isRecipe(key) && !system.isSlotRecipe(key);
		});

		expect(missing).toEqual([]);
	});
});
