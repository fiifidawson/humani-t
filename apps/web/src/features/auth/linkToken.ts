/**
 * The one-time token an emailed link carries as `#token=…`.
 *
 * A fragment never reaches a server, a log or a Referer. Once read it is also
 * dropped from the address bar, so the token is not left in the history.
 */

import { useLocation, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

/** The token from the link, or `null` when the link carried none. */
export function useLinkToken(): string | null {
	const location = useLocation();
	const navigate = useNavigate();
	const [token] = useState(
		() => new URLSearchParams(location.hash).get("token") || null,
	);

	useEffect(() => {
		if (location.hash !== "") {
			void navigate({ href: location.pathname, replace: true });
		}
	}, [location.hash, location.pathname, navigate]);

	return token;
}
