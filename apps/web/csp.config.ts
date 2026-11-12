/**
 * The Content-Security-Policy, and how each server puts a nonce in it.
 *
 * Caddy is the source of truth in production; src/csp.test.ts fails if its header
 * differs from `contentSecurityPolicy()`. Styles need a nonce because Chakra writes
 * them at run time (docs/adr/0004); scripts stay `'self'` only. The build writes
 * NONCE_TEMPLATE where the nonce goes, and Caddy or `previewNonce` fills it per page.
 */

import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Plugin, ResolvedConfig } from "vite";

/** Caddy's per-request UUIDv4 (122 random bits), the same in header and body. */
export const CADDY_NONCE_PLACEHOLDER = "{http.request.uuid}";

/** A Go template for Caddy's `templates`; backquotes, so no `"` inside an attribute. */
export const NONCE_TEMPLATE = "{{placeholder `http.request.uuid`}}";

/**
 * The dev server's nonce. Fixed, because the dev server renders index.html
 * without a template step; it is never used outside localhost.
 */
export const DEV_NONCE = "humani-t-dev";

/** The production policy with `nonce` allowed for styles. */
export function contentSecurityPolicy(nonce: string): string {
	return [
		"default-src 'none'",
		"script-src 'self'",
		`style-src 'self' 'nonce-${nonce}'`,
		"img-src 'self'",
		"font-src 'self'",
		"connect-src 'self'",
		"manifest-src 'self'",
		"base-uri 'none'",
		"form-action 'self'",
		"frame-ancestors 'none'",
		"object-src 'none'",
		"require-trusted-types-for 'script'",
		"trusted-types 'none'",
		"upgrade-insecure-requests",
	].join("; ");
}

/**
 * The dev server's policy: production's, relaxed for React Refresh and hot reload.
 * Trusted Types is report-only here because Vite's error overlay writes innerHTML.
 */
export function devContentSecurityPolicy(port = 5173): string {
	return contentSecurityPolicy(DEV_NONCE)
		.replace("script-src 'self'", `script-src 'self' 'nonce-${DEV_NONCE}'`)
		.replace("connect-src 'self'", `connect-src 'self' ws://localhost:${port}`)
		.replace("; require-trusted-types-for 'script'; trusted-types 'none'", "")
		.replace("; upgrade-insecure-requests", "");
}

/** Trusted Types in development: violations are reported in the console, not blocked. */
export const DEV_REPORT_ONLY =
	"require-trusted-types-for 'script'; trusted-types 'none'";

/** Put `nonce` where the build left NONCE_TEMPLATE: what Caddy's `templates` does. */
export function withNonce(html: string, nonce: string): string {
	return html.replaceAll(NONCE_TEMPLATE, nonce);
}

function isPageRequest(url: string | undefined): boolean {
	const path = (url ?? "/").split("?")[0] ?? "/";
	return (
		!path.startsWith("/api/") &&
		!/\.[a-z0-9]+$/i.test(path.replace(/\/index\.html$/, ""))
	);
}

/**
 * Set the nonce for each command: a Caddy template in the build, the fixed dev
 * nonce in the dev server. Serve each with its policy, and give `vite preview`
 * a fresh nonce per page, as Caddy does.
 */
export function cspNonce(): Plugin {
	let config: ResolvedConfig | null = null;
	return {
		name: "humani-t:csp-nonce",
		config(_, { command }) {
			return {
				html: { cspNonce: command === "build" ? NONCE_TEMPLATE : DEV_NONCE },
			};
		},
		configResolved(resolved) {
			config = resolved;
		},
		configureServer(server) {
			server.middlewares.use((_request, response, next) => {
				response.setHeader(
					"Content-Security-Policy",
					devContentSecurityPolicy(server.config.server.port),
				);
				response.setHeader(
					"Content-Security-Policy-Report-Only",
					DEV_REPORT_ONLY,
				);
				next();
			});
		},
		configurePreviewServer(server) {
			server.middlewares.use(async (request, response, next) => {
				const nonce = randomUUID();
				response.setHeader(
					"Content-Security-Policy",
					contentSecurityPolicy(nonce),
				);
				if (
					config === null ||
					request.method !== "GET" ||
					!isPageRequest(request.url)
				) {
					next();
					return;
				}
				try {
					const page = await readFile(
						join(config.root, config.build.outDir, "index.html"),
						"utf8",
					);
					response.setHeader("Content-Type", "text/html; charset=utf-8");
					response.setHeader("Cache-Control", "no-store");
					response.end(withNonce(page, nonce));
				} catch (error) {
					next(error);
				}
			});
		},
	};
}
