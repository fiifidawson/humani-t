/**
 * The browser entry point. Everything here also runs under the production
 * CSP: no inline script, styles only under the page's nonce, nothing from
 * another origin.
 */

import "@fontsource-variable/manrope";
import "./theme/fonts.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createApiClient } from "./api/client";
import { App } from "./app/App";
import { applyTheme, followSystemTheme, readTheme } from "./app/preferences";
import { createQueryClient } from "./app/queryClient";
import { createAppRouter } from "./app/router";

// Before the first render, so a dark-theme choice does not flash light.
applyTheme(readTheme());
followSystemTheme();

const root = document.getElementById("root");
if (root === null) {
	throw new Error("index.html has no #root element");
}

const api = createApiClient();
const queryClient = createQueryClient();
const router = createAppRouter({ api, queryClient });

createRoot(root).render(
	<StrictMode>
		<App api={api} queryClient={queryClient} router={router} />
	</StrictMode>,
);
