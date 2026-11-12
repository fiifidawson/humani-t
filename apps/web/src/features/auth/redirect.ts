/**
 * Where to go after signing in: a path on this site, or nowhere.
 * Anyone can put `redirect` in a link, so `//evil.example` would be an open redirect.
 */
export function safeRedirect(value: unknown): string | undefined {
	if (
		typeof value !== "string" ||
		!value.startsWith("/") ||
		value.startsWith("//") ||
		value.includes("\\")
	) {
		return undefined;
	}
	const url = new URL(value, window.location.origin);
	if (
		url.origin !== window.location.origin ||
		url.pathname.startsWith("/sign-in")
	) {
		return undefined;
	}
	return `${url.pathname}${url.search}${url.hash}`;
}
