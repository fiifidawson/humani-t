import { createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { createApiClient } from "../api/client";
import { App } from "../app/App";
import { createQueryClient } from "../app/queryClient";
import { type AppRouter, createAppRouter } from "../app/router";
import { BrandProvider } from "../theme/BrandProvider";

/** Render the whole app, as a browser would, starting at `path`. */
export function renderApp(path: string) {
	const api = createApiClient();
	const queryClient = createQueryClient();
	const history = createMemoryHistory({ initialEntries: [path] });
	const router = createAppRouter({ api, queryClient, history });
	const user = userEvent.setup();
	const view = render(
		<App api={api} queryClient={queryClient} router={router} />,
	);
	return { ...view, user, router };
}

export function currentPath(router: AppRouter): string {
	return router.state.location.pathname;
}

/** Render one component inside the brand's Chakra system, as the app does. */
export function renderWithBrand(ui: ReactElement) {
	return render(ui, { wrapper: BrandProvider });
}
