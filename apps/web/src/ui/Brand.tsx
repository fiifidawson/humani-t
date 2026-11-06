/**
 * The two logo slots of LiGHT's sub-project rule: the project's logo on the
 * left, LiGHT's on the right. Text in Manrope until the official SVGs are
 * dropped into public/brand/ (see public/brand/README.md); the LiGHT wordmark
 * is never drawn here.
 */

import { Text } from "@chakra-ui/react";

interface MarkProps {
	/** On a brand-colour background, where only Eggshell may be used for text. */
	isOnColour?: boolean;
}

/** Humani-T's logo slot. */
export function ProjectMark({
	size = "lg",
	isOnColour = false,
}: MarkProps & { size?: "md" | "lg" | "xl" | "2xl" }) {
	return (
		<Text
			as="span"
			fontWeight="bold"
			fontSize={size}
			letterSpacing="-0.01em"
			lineHeight="1"
			color={isOnColour ? "light.eggshell" : undefined}
		>
			Humani-T
		</Text>
	);
}

/** LiGHT's logo slot. */
export function LabMark({ isOnColour = false }: MarkProps) {
	return (
		<Text
			as="span"
			fontSize="sm"
			lineHeight="1"
			whiteSpace="nowrap"
			color={isOnColour ? "light.eggshell" : "fg.muted"}
		>
			by{" "}
			<Text
				as="span"
				fontWeight="bold"
				color={isOnColour ? "light.eggshell" : "fg"}
			>
				LiGHT
			</Text>
		</Text>
	);
}
