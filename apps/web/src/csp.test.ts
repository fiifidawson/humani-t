/**
 * The app must run under a CSP with no 'unsafe-inline', styles only under a
 * per-response nonce, and Trusted Types enforced (csp.config.ts; Caddy sends it
 * in production). These checks keep the source from drifting into something
 * that policy would block or weaken, and Caddy's copy from drifting from ours.
 *
 * Chakra UI's styling passes: its style props become class names, and Emotion
 * writes the rules into <style nonce> elements, not into `style` attributes.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import {
	CADDY_NONCE_PLACEHOLDER,
	contentSecurityPolicy,
	DEV_NONCE,
	devContentSecurityPolicy,
	NONCE_TEMPLATE,
	withNonce,
} from "../csp.config";

const webRoot = join(import.meta.dirname, "..");
const sourceRoot = join(webRoot, "src");
const caddyfile = readFileSync(
	join(webRoot, "..", "..", "infrastructure", "caddy", "Caddyfile"),
	"utf8",
);

function sourceFiles(directory: string): string[] {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) {
			return entry.name === "test" ? [] : sourceFiles(path);
		}
		const isApplicationCode =
			/\.(ts|tsx|css)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name);
		return isApplicationCode && !entry.name.endsWith(".gen.ts") ? [path] : [];
	});
}

function filesMatching(pattern: RegExp): string[] {
	return sourceFiles(sourceRoot)
		.filter((path) => pattern.test(readFileSync(path, "utf8")))
		.map((path) => relative(webRoot, path));
}

/** The `handle` block Caddy serves the app's pages from, after /assets. */
function pageBlock(): string {
	const start = caddyfile.indexOf(
		"# Every other path is a route inside the app",
	);
	return caddyfile.slice(start, caddyfile.indexOf("\n\t\t}", start));
}

describe("nothing a strict CSP would block", () => {
	it.each([
		["inline style props", /\bstyle=\{/],
		["dangerouslySetInnerHTML", /dangerouslySetInnerHTML/],
		[
			"HTML string sinks",
			/\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write/,
		],
		["eval or Function constructors", /\beval\(|new Function\(/],
		[
			"inline style writes through the DOM",
			/\.style\.[a-zA-Z]+\s*=|setProperty\(\s*["']--/,
		],
	])("no %s in application code", (_, pattern) => {
		expect(filesMatching(pattern)).toEqual([]);
	});

	it("index.html has no inline script or style", () => {
		const html = readFileSync(join(webRoot, "index.html"), "utf8");

		expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
		expect(html).not.toMatch(/<style|\sstyle=/);
	});

	it("loads nothing from another origin", () => {
		expect(
			filesMatching(
				/https?:\/\/(?!localhost|127\.0\.0\.1|www\.w3\.org\/2000\/svg)/,
			),
		).toEqual([]);
	});
});

describe("the policy", () => {
	it("allows styles only from this origin or with the nonce, and never inline", () => {
		const policy = contentSecurityPolicy("abc");

		expect(policy).toContain("style-src 'self' 'nonce-abc';");
		expect(policy).toContain("script-src 'self';");
		expect(policy).not.toContain("unsafe-inline");
		expect(policy).not.toContain("unsafe-eval");
		expect(policy).toContain("require-trusted-types-for 'script'");
		expect(policy).toContain("trusted-types 'none'");
	});

	it("is exactly what Caddy sends, with Caddy's per-request nonce", () => {
		const served = [
			...caddyfile.matchAll(/^\s*header Content-Security-Policy "([^"]+)"/gm),
		].map((match) => match[1]);

		expect(served).toEqual([contentSecurityPolicy(CADDY_NONCE_PLACEHOLDER)]);
	});

	it("is relaxed for the dev server only where hot reload needs it", () => {
		const dev = devContentSecurityPolicy();

		expect(dev).toContain(`script-src 'self' 'nonce-${DEV_NONCE}';`);
		expect(dev).toContain(`style-src 'self' 'nonce-${DEV_NONCE}';`);
		expect(dev).not.toContain("unsafe-inline");
		expect(dev).toContain("frame-ancestors 'none'");
	});
});

describe("the nonce Caddy puts in the page", () => {
	it("comes from the placeholder the header uses", () => {
		const header = CADDY_NONCE_PLACEHOLDER.slice(1, -1);

		expect(NONCE_TEMPLATE).toBe(`{{placeholder \`${header}\`}}`);
	});

	it("is filled in only for the page itself, never for hashed assets", () => {
		const assets = caddyfile.slice(
			caddyfile.indexOf("handle /assets/*"),
			caddyfile.indexOf("# Every other path is a route inside the app"),
		);

		expect(assets).not.toMatch(/templates/);
		expect(pageBlock()).toMatch(/@page path \/ \/index\.html/);
		expect(pageBlock()).toMatch(/^\s*templates @page$/m);
	});

	it("is never served from a cache, where it would no longer match the header", () => {
		expect(pageBlock()).toMatch(/header Cache-Control "no-store"/);
	});

	it("replaces every placeholder in the page with the response's nonce", () => {
		const page = `<meta property="csp-nonce" nonce="${NONCE_TEMPLATE}"><script type="module" nonce="${NONCE_TEMPLATE}" src="/assets/index.js"></script>`;
		const nonce = "4c2f0d1e-8a3b-4b7c-9d2e-1f0a3b5c7d9e";

		const filled = withNonce(page, nonce);

		expect(filled).not.toContain("{{");
		expect([...filled.matchAll(/nonce="([^"]+)"/g)].map((m) => m[1])).toEqual([
			nonce,
			nonce,
		]);
		expect(contentSecurityPolicy(nonce)).toContain(`'nonce-${nonce}'`);
	});
});
