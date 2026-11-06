/** Up to two capital initials from a name's first words, or "?" for a blank one. */
export function initials(name: string): string {
	return (
		name
			.split(/\s+/)
			.filter((part) => part !== "")
			.slice(0, 2)
			.map((part) => Array.from(part)[0]?.toUpperCase() ?? "")
			.join("") || "?"
	);
}
