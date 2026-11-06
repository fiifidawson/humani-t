/**
 * Where to go after signing in: a path on this site, or nowhere.
 *
 * The `redirect` search parameter is in the URL, so anyone can write it into a
 * link. Following `//evil.example` after a sign-in would hand a freshly
 * signed-in person to another site (an open redirect, OWASP ASVS 5.0 V3).
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
