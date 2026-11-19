/**
 * Playwright: the web app in a real browser, against a real API.
 *
 * The API runs in its development demo mode: in-memory storage, a stand-in model
 * and made-up papers, so nothing is sent anywhere and every run starts empty. It
 * and Vite use their own ports, so a developer's servers on 8731 and 5173 can keep running.
 */

import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8741;
const WEB_PORT = 5183;
const WEB_ORIGIN = `http://localhost:${WEB_PORT}`;

export const ADMIN_EMAIL = "e2e@example.org";
export const ADMIN_PASSWORD = "violet-harbour-quietly-41-lanterns";
export const SIGNED_IN = "test-results/.auth/admin.json";

export default defineConfig({
	testDir: "e2e",
	fullyParallel: false,
	workers: 1,
	forbidOnly: Boolean(process.env["CI"]),
	retries: process.env["CI"] ? 1 : 0,
	reporter: process.env["CI"]
		? [["github"], ["html", { open: "never" }]]
		: "list",
	use: {
		baseURL: WEB_ORIGIN,
		trace: "retain-on-failure",
		screenshot: "only-on-failure",
	},
	projects: [
		{ name: "sign-in", testMatch: /sign-in\.setup\.ts/ },
		{
			name: "chromium",
			use: { ...devices["Desktop Chrome"], storageState: SIGNED_IN },
			dependencies: ["sign-in"],
		},
	],
	webServer: [
		{
			command: `uv run uvicorn app.main:app --port ${API_PORT} --no-proxy-headers`,
			cwd: "../../services/api",
			url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
			reuseExistingServer: false,
			timeout: 120_000,
			env: {
				HUMANI_T_ENVIRONMENT: "development",
				HUMANI_T_PUBLIC_ORIGIN: WEB_ORIGIN,
				HUMANI_T_DEMO_INFERENCE: "true",
				HUMANI_T_PAPER_SOURCES: "demo",
				HUMANI_T_DEV_ADMIN_EMAIL: ADMIN_EMAIL,
				HUMANI_T_DEV_ADMIN_PASSWORD: ADMIN_PASSWORD,
			},
		},
		{
			command: `pnpm exec vite --port ${WEB_PORT}`,
			url: WEB_ORIGIN,
			reuseExistingServer: false,
			timeout: 120_000,
			env: { HUMANI_T_API_TARGET: `http://127.0.0.1:${API_PORT}` },
		},
	],
});
