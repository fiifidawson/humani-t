/**
 * "Firefox on Windows" from a User-Agent string, for the devices list.
 *
 * A label for people, not detection: it only has to be recognisable, and an
 * unusual browser falls back to "Unknown browser" rather than being guessed.
 */

const BROWSERS: readonly [RegExp, string][] = [
	[/Edg\//, "Edge"],
	[/Firefox\//, "Firefox"],
	[/Chrome\//, "Chrome"],
	[/Safari\//, "Safari"],
];

const SYSTEMS: readonly [RegExp, string][] = [
	[/iPhone/, "iPhone"],
	[/iPad/, "iPad"],
	[/Android/, "Android"],
	[/Windows/, "Windows"],
	[/Mac OS X|Macintosh/, "macOS"],
	[/Linux|X11/, "Linux"],
];

const first = (table: readonly [RegExp, string][], text: string) =>
	table.find(([pattern]) => pattern.test(text))?.[1];

export function describeDevice(userAgent: string | null): string {
	if (userAgent === null || userAgent === "") {
		return "Unknown device";
	}
	const browser = first(BROWSERS, userAgent) ?? "Unknown browser";
	const system = first(SYSTEMS, userAgent);
	return system === undefined ? browser : `${browser} on ${system}`;
}
