import { Heading } from "@chakra-ui/react";
import { type ReactNode, useEffect, useRef } from "react";

const SIZES = {
	page: { base: "3xl", md: "4xl" },
	auth: "3xl",
	workspace: { base: "xl", md: "2xl" },
} as const;

interface PageHeadingProps {
	/** The tab title, before " · Humani-T". */
	title: string;
	children?: ReactNode;
	size?: keyof typeof SIZES;
	id?: string;
}

/**
 * The page's h1. It sets the tab title and takes focus when a navigation
 * opens the page, so a screen reader announces where a link led (a
 * single-page app gets no page load to do that for it).
 */
export function PageHeading({
	title,
	children,
	size = "page",
	id,
}: PageHeadingProps) {
	const heading = useRef<HTMLHeadingElement>(null);

	useEffect(() => {
		document.title = `${title} · Humani-T`;
		// Only when focus was somewhere: after a click or a submit, the element
		// that had it may be gone. On a fresh page load focus stays at the
		// top, so the first Tab still reaches the skip link.
		if (
			document.activeElement !== null &&
			document.activeElement !== document.body
		) {
			heading.current?.focus({ preventScroll: true });
		}
	}, [title]);

	return (
		<Heading
			as="h1"
			ref={heading}
			id={id}
			tabIndex={-1}
			fontSize={SIZES[size]}
			lineHeight="1.2"
			fontWeight="semibold"
			minW="0"
			overflowWrap="anywhere"
		>
			{children ?? title}
		</Heading>
	);
}
