/**
 * The preview card chat apps show for a shared link (Open Graph and Twitter tags).
 * The address in the tags is each deployment's own, filled in by Caddy from the
 * domain the operator set, so no address is written into the code.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ORIGIN_TEMPLATE, withOrigin } from "../csp.config";

const webRoot = join(import.meta.dirname, "..");
const repoRoot = join(webRoot, "..", "..");
const page = new DOMParser().parseFromString(
	readFileSync(join(webRoot, "index.html"), "utf8"),
	"text/html",
);

function tag(key: string): string | null {
	const meta =
		page.querySelector(`meta[property="${key}"]`) ??
		page.querySelector(`meta[name="${key}"]`);
	return meta?.getAttribute("content") ?? null;
}

/** Width and height from a PNG's IHDR chunk, or null if it is not a PNG. */
function pngSize(path: string): [number, number] | null {
	const bytes = readFileSync(path);
	const signature = "89504e470d0a1a0a";
	if (bytes.subarray(0, 8).toString("hex") !== signature) {
		return null;
	}
	return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe("the link preview card", () => {
	it.each([
		["og:type", "website"],
		["og:site_name", "Humani-T"],
		["og:title", "Humani-T"],
		["twitter:card", "summary_large_image"],
		["twitter:title", "Humani-T"],
		["og:image:type", "image/png"],
		["og:image:width", "1200"],
		["og:image:height", "630"],
	])("sets %s to %s", (key, value) => {
		expect(tag(key)).toBe(value);
	});

	it("describes the product in the same words on every network", () => {
		const description = tag("og:description");

		expect(description).toMatch(/evidence/i);
		expect(tag("twitter:description")).toBe(description);
		expect(tag("description")).toBe(description);
	});

	it("points at the deployment's own address, never a fixed one", () => {
		expect(tag("og:url")).toBe(`${ORIGIN_TEMPLATE}/`);
		expect(tag("og:image")).toBe(`${ORIGIN_TEMPLATE}/og-image.png`);
		expect(tag("twitter:image")).toBe(tag("og:image"));
	});

	it("describes the image for people who cannot see it", () => {
		expect(tag("og:image:alt")).toBeTruthy();
		expect(tag("twitter:image:alt")).toBe(tag("og:image:alt"));
	});

	it("ships a 1200 by 630 PNG, which every scraper accepts", () => {
		expect(pngSize(join(webRoot, "public", "og-image.png"))).toEqual([
			1200, 630,
		]);
	});
});

describe("the address in the card", () => {
	it("comes from the domain the operator set, the one Caddy serves", () => {
		const caddyfile = readFileSync(
			join(repoRoot, "infrastructure", "caddy", "Caddyfile"),
			"utf8",
		);
		const compose = readFileSync(join(repoRoot, "compose.yaml"), "utf8");

		expect(ORIGIN_TEMPLATE).toBe("https://{{env `HUMANI_T_DOMAIN`}}");
		expect(caddyfile).toMatch(/^\{\$HUMANI_T_DOMAIN:localhost\} \{$/m);
		expect(compose).toMatch(/HUMANI_T_DOMAIN: \$\{HUMANI_T_DOMAIN/);
	});

	it("is filled in by the preview server as Caddy fills it in production", () => {
		const html = `<meta property="og:url" content="${ORIGIN_TEMPLATE}/">`;

		expect(withOrigin(html, "http://localhost:4173")).toBe(
			'<meta property="og:url" content="http://localhost:4173/">',
		);
	});
});
