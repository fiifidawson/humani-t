/**
 * The root component: providers, and the one place that notices a session
 * ending under the page.
 */

import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import type { ApiClient } from "../api/client";
import { isApiError } from "../api/problem";
import { ReauthProvider } from "../features/auth/ReauthDialog";
import { BrandProvider } from "../theme/BrandProvider";
import { ApiProvider } from "./api-context";
import { MemoryProvider, PageMemory } from "./memory";
import { keys } from "./queries";
import type { AppRouter } from "./router";

interface AppProps {
	api: ApiClient;
	queryClient: QueryClient;
	router: AppRouter;
}

/**
 * When any request answers a plain 401, the session has ended (idle timeout,
 * revoked from another device). Say so, and forget the cached session so the
 * next navigation goes through sign-in. The page itself stays as it was.
 */
function useSessionEndWatcher(
	queryClient: QueryClient,
	memory: PageMemory,
): void {
	useEffect(() => {
		const onError = (error: unknown) => {
			if (
				isApiError(error, "signed-out") &&
				queryClient.getQueryData(keys.session) !== undefined
			) {
				memory.isSignedOut.set(true);
				queryClient.removeQueries({ queryKey: keys.session });
			}
		};
		const stopQueries = queryClient.getQueryCache().subscribe((event) => {
			if (event.type === "updated" && event.action.type === "error") {
				onError(event.action.error);
			}
		});
		const stopMutations = queryClient.getMutationCache().subscribe((event) => {
			if (event.type === "updated" && event.action.type === "error") {
				onError(event.action.error);
			}
		});
		return () => {
			stopQueries();
			stopMutations();
		};
	}, [queryClient, memory]);
}

export function App({ api, queryClient, router }: AppProps) {
	const [memory] = useState(() => new PageMemory());
	useSessionEndWatcher(queryClient, memory);

	return (
		<BrandProvider>
			<ApiProvider api={api}>
				<QueryClientProvider client={queryClient}>
					<MemoryProvider memory={memory}>
						<ReauthProvider>
							<RouterProvider router={router} />
						</ReauthProvider>
					</MemoryProvider>
				</QueryClientProvider>
			</ApiProvider>
		</BrandProvider>
	);
}
