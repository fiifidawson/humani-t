/**
 * Generate, or check, the web app's API types from the API's own code.
 *
 *   node scripts/api-types.ts generate   write services/api/openapi.json and src/api/schema.gen.ts
 *   node scripts/api-types.ts check      fail if either committed file is stale
 *
 * Both steps need `uv` and the API's Python environment, because the OpenAPI
 * document is exported from `create_app()` rather than fetched from a server.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import openapiTS, {
	astToString,
	COMMENT_HEADER,
	UNKNOWN,
} from "openapi-typescript";

const repositoryRoot = resolve(import.meta.dirname, "..", "..", "..");
const apiDirectory = join(repositoryRoot, "services", "api");
const exportScript = join(repositoryRoot, "scripts", "export_openapi.py");
const committedDocument = join(apiDirectory, "openapi.json");
const committedTypes = join(
	repositoryRoot,
	"apps",
	"web",
	"src",
	"api",
	"schema.gen.ts",
);

function exportDocument(output: string): void {
	const result = spawnSync(
		"uv",
		[
			"run",
			"--directory",
			apiDirectory,
			"python",
			exportScript,
			"--output",
			output,
		],
		{ stdio: "inherit" },
	);
	if (result.error !== undefined) {
		throw new Error(
			`could not run uv (is it installed?): ${result.error.message}`,
		);
	}
	if (result.status !== 0) {
		throw new Error(
			`exporting the OpenAPI document failed with exit code ${result.status}`,
		);
	}
}

async function typesFor(document: string): Promise<string> {
	const ast = await openapiTS(pathToFileURL(document), {
		silent: true,
		// The API's JsonValue is recursive, and openapi-typescript writes it as
		// an alias that refers to itself, which tsc rejects (TS2502). `unknown`
		// is the honest type for arbitrary JSON: callers must narrow it.
		transform: (_, { path }) =>
			path?.endsWith("/components/schemas/JsonValue") ? UNKNOWN : undefined,
	});
	return `${COMMENT_HEADER}${astToString(ast)}`;
}

function readOrEmpty(path: string): string {
	try {
		return readFileSync(path, "utf8");
	} catch {
		return "";
	}
}

async function generate(): Promise<void> {
	exportDocument(committedDocument);
	writeFileSync(committedTypes, await typesFor(committedDocument), "utf8");
	console.log(`wrote ${committedDocument}\nwrote ${committedTypes}`);
}

async function check(): Promise<boolean> {
	const scratch = mkdtempSync(join(tmpdir(), "humani-t-api-"));
	try {
		const freshDocument = join(scratch, "openapi.json");
		exportDocument(freshDocument);
		const stale: string[] = [];
		if (
			readFileSync(freshDocument, "utf8") !== readOrEmpty(committedDocument)
		) {
			stale.push(committedDocument);
		}
		if ((await typesFor(freshDocument)) !== readOrEmpty(committedTypes)) {
			stale.push(committedTypes);
		}
		if (stale.length > 0) {
			console.error(
				`Stale, run \`pnpm gen:api\` and commit:\n  ${stale.join("\n  ")}`,
			);
			return false;
		}
		console.log("API document and generated types are up to date.");
		return true;
	} finally {
		rmSync(scratch, { recursive: true, force: true });
	}
}

const mode = process.argv[2];
if (mode === "generate") {
	await generate();
} else if (mode === "check") {
	process.exitCode = (await check()) ? 0 : 1;
} else {
	console.error("usage: node scripts/api-types.ts generate|check");
	process.exitCode = 2;
}
