import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "../api/problem";

/**
 * Retry only where waiting can help (UI_UX_SPEC §11.4): a dropped connection,
 * twice, and a 429 or 503 that said when to come back, once, at that time.
 * Every 4xx is an answer, not a glitch, and is shown at once.
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: 30_000,
				retry: (failures, error) =>
					(isApiError(error, "offline") && failures < 2) ||
					(isApiError(error, "throttled") && failures < 1),
				retryDelay: (failures, error) =>
					isApiError(error, "throttled") && error.retryAfterSeconds !== null
						? error.retryAfterSeconds * 1000
						: Math.min(1000 * 2 ** failures, 8000),
			},
			mutations: { retry: false },
		},
	});
}
