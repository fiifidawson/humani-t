/**
 * Vite: the dev server, the production build and `vite preview`.
 *
 * In development Vite forwards `/api` to 127.0.0.1:8731, so the SPA and API share
 * one origin as behind Caddy; start the API with
 * HUMANI_T_PUBLIC_ORIGIN=http://localhost:5173. Both servers send a CSP
 * (csp.config.ts): `vite preview` production's, the dev server a relaxed one.
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
