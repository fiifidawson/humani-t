/**
 * Vite: the dev server, the production build and `vite preview`.
 *
 * In development the browser talks only to Vite, which forwards `/api` to the
 * API on 127.0.0.1:8731. The SPA and the API then share one origin, exactly as
 * they do behind Caddy in production, so cookies and the CSRF checks behave the
 * same. That origin is Vite's, so the API must be started with
 * HUMANI_T_PUBLIC_ORIGIN=http://localhost:5173 (see docs/walkthroughs/13-web-ui.md).
 *
 * Both servers send a Content-Security-Policy (csp.config.ts): `vite preview`
 * the production one with a fresh nonce per page, the dev server a relaxed one.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { cspNonce } from "./csp.config.ts";

const API_TARGET = "http://127.0.0.1:8731";

const apiProxy = {
	"/api": {
		target: API_TARGET,
		// Keep the browser's Host header: the API should see the request as the
		// browser sent it, not as the proxy rewrote it.
		changeOrigin: false,
	},
};

export default defineConfig({
	plugins: [react(), cspNonce()],
	server: {
		host: "localhost",
		port: 5173,
		strictPort: true,
		proxy: apiProxy,
	},
	preview: {
		host: "localhost",
		port: 4173,
		strictPort: true,
		proxy: apiProxy,
	},
	build: {
		target: "es2022",
		// Never inline assets as data: URIs. The CSP allows only same-origin
		// images and fonts, so an inlined font would silently fail to load.
		assetsInlineLimit: 0,
	},
});
