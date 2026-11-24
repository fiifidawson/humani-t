/**
 * The two logo slots of LiGHT's sub-project rule: the project's logo on the
 * left, LiGHT's on the right. LiGHT's wordmark is the lab's own file
 * (src/assets/brand/README.md), never redrawn; humani-t's is text in Manrope
 * until it has a logo of its own.
 */

import { Image, type ImageProps, Link, Text } from "@chakra-ui/react";
import lightLogo from "../assets/brand/light-logo.svg";
import lightLogoWhite from "../assets/brand/light-logo-white.svg";

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

// Smaller than the project's name: beside a sub-project, LiGHT's logo is secondary.
const LOGO_HEIGHT = "16px";

function Wordmark({
	src,
	...props
}: { src: string } & Pick<ImageProps, "display" | "_dark">) {
	return (
		<Image
			src={src}
			alt="LiGHT"
			h={LOGO_HEIGHT}
			w="auto"
			flex="none"
			htmlWidth="1421"
			htmlHeight="397"
			// Lazy, so the browser never fetches the variant the theme hides.
			loading="lazy"
			{...props}
		/>
	);
}

/**
 * LiGHT's logo slot: its wordmark in the original blues, or white on colour and
 * in the dark theme, linking to the lab's site.
 */
export function LabMark({ isOnColour = false }: MarkProps) {
	return (
		<Link
			href="https://www.light-laboratory.org/"
			target="_blank"
			rel="noopener noreferrer"
			flex="none"
			rounded="sm"
		>
			{isOnColour ? (
				<Wordmark src={lightLogoWhite} />
			) : (
				<>
					<Wordmark src={lightLogo} _dark={{ display: "none" }} />
					<Wordmark
						src={lightLogoWhite}
						display="none"
						_dark={{ display: "block" }}
					/>
				</>
			)}
		</Link>
	);
}
