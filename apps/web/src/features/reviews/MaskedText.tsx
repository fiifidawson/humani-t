/**
 * Masked text with each placeholder drawn as a small tag (UI_UX_SPEC §5.3.2,
 * §8.5): "Location 1" on a tinted rectangle, read as "masked location 1".
 */

import { chakra, Text, VisuallyHidden } from "@chakra-ui/react";
import { shownPlaceholder, splitMasked, spokenPlaceholder } from "./masking";

const Pill = chakra("span", {
	base: {
		display: "inline-block",
		px: "1.5",
		mx: "0.5",
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

/** `isInline` for text inside a sentence or a list item, where a paragraph cannot go. */
export function MaskedText({
	text,
	isInline = false,
}: {
	text: string;
	isInline?: boolean;
}) {
	return (
		<Text
			as={isInline ? "span" : "p"}
			whiteSpace="pre-wrap"
			overflowWrap="anywhere"
		>
			{splitMasked(text).map((part, index) =>
				typeof part === "string" ? (
					part
				) : (
					// biome-ignore lint/suspicious/noArrayIndexKey: parts have no identity but their place
					<Pill key={index}>
						<span aria-hidden="true">{shownPlaceholder(part)}</span>
						<VisuallyHidden>{spokenPlaceholder(part)}</VisuallyHidden>
					</Pill>
				),
			)}
		</Text>
	);
}
