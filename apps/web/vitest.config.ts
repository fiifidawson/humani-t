import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config.ts";

export default mergeConfig(
	viteConfig,
	defineConfig({
		test: {
			environment: "jsdom",
			// https, so `Secure` cookies such as __Host-csrf can be set as a
			// browser would set them.
			environmentOptions: { jsdom: { url: "https://localhost/" } },
			setupFiles: ["./src/test/setup.ts"],
			include: ["src/**/*.test.{ts,tsx}"],
			// Whole-app tests sign in, navigate and wait on MSW; on a loaded
			// Windows machine a few take longer than the default 5 s.
			testTimeout: 30_000,
			restoreMocks: true,
			unstubGlobals: true,
		},
	}),
);
