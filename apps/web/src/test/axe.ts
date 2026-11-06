import axe from "axe-core";
import { expect } from "vitest";

/**
 * Fail with a readable list if axe finds any WCAG problem in `root`.
 *
 * Colour contrast is off: jsdom does no layout and applies no stylesheet, so
 * axe cannot measure it. Contrast is checked against the tokens by hand.
 */
export async function expectNoAccessibilityViolations(
	root: Element = document.body,
): Promise<void> {
	const results = await axe.run(root, {
		runOnly: {
			type: "tag",
			values: [
				"wcag2a",
				"wcag2aa",
				"wcag21a",
				"wcag21aa",
				"wcag22aa",
				"best-practice",
			],
		},
		rules: { "color-contrast": { enabled: false } },
	});
	const problems = results.violations.flatMap((violation) =>
		violation.nodes.map(
			(node) =>
				`${violation.id}: ${violation.help} at ${node.target.join(" ")}`,
		),
	);
	expect(problems).toEqual([]);
}
