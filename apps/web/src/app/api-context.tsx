import { createContext, type ReactNode, useContext } from "react";
import type { ApiClient } from "../api/client";

const ApiContext = createContext<ApiClient | null>(null);

export function ApiProvider({
	api,
	children,
}: {
	api: ApiClient;
	children: ReactNode;
}) {
	return <ApiContext value={api}>{children}</ApiContext>;
}

export function useApi(): ApiClient {
	const api = useContext(ApiContext);
	if (api === null) {
		throw new Error("useApi must be used inside <ApiProvider>");
	}
	return api;
}
