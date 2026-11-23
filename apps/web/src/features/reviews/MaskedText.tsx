/**
 * Masked text with each placeholder drawn as a small tag (UI_UX_SPEC §5.3.2,
 * §8.5): "Location 1" on a tinted rectangle, read as "masked location 1".
 */

import { chakra, Text, VisuallyHidden } from "@chakra-ui/react";
import { shownPlaceholder, splitMasked, spokenPlaceholder } from "./masking";

const Pill = chakra("span", {
	base: {
		display: "inline-block",
		// No outer margin: a tag sits against punctuation, as the word it replaced did.
		px: "1",
		rounded: "sm",
		bg: "masked.bg",
		color: "masked.fg",
		fontSize: "0.85em",
		fontWeight: "semibold",
		lineHeight: "1.5",
		whiteSpace: "nowrap",
		verticalAlign: "baseline",
	},
});

/**
 * `isInline` for text inside a sentence or a list item, where a paragraph cannot go.
 * `whiteSpace` "normal" lets a PDF's printed lines flow back together.
 */
export function MaskedText({
	text,
	isInline = false,
	whiteSpace = "pre-wrap",
}: {
	text: string;
	isInline?: boolean;
	whiteSpace?: "pre-wrap" | "pre-line" | "normal";
}) {
	return (
		<Text
			as={isInline ? "span" : "p"}
			whiteSpace={whiteSpace}
			overflowWrap="anywhere"
		>
			{splitMasked(text).map((part, index) =>
				typeof part === "string" ? (
					part
				) : (
					// biome-ignore lint/suspicious/noArrayIndexKey: parts have no identity but their place
					<Pill key={index} data-placeholder="">
						<span aria-hidden="true">{shownPlaceholder(part)}</span>
						<VisuallyHidden>{spokenPlaceholder(part)}</VisuallyHidden>
					</Pill>
				),
			)}
		</Text>
	);
}
