/**
 * The frame every chat card shares (UI_UX_SPEC §5.2, §10.2 `ChatTurn`): a
 * heading that names the card, who made it, when, the body, and at most one
 * main action and two others. One-line forms (L0) use `ChatLine` instead.
 */

import { Box, Flex, Heading, Text, VisuallyHidden } from "@chakra-ui/react";
import { type ReactNode, useId } from "react";
import { fullDate } from "../projects/format";
import { relativeTime } from "./format";

interface ChatCardProps {
	title: string;
	/** Who made it: a person, or which part of Humani-T (§5.2 "Authors"). */
	author?: string | undefined;
	at?: string | undefined;
	tone?: "plain" | "person" | "warning" | "error";
	children?: ReactNode;
	actions?: ReactNode;
}

const TONES = {
	plain: { bg: "bg.panel", borderColor: "border" },
	person: { bg: "bg.muted", borderColor: "border" },
	warning: { bg: "caveat.subtle", borderColor: "border.warning" },
	error: { bg: "danger.subtle", borderColor: "border.error" },
} as const;

/** "5 min ago" to see, the full date and time on hover and for screen readers (§5.2). */
export function When({ at }: { at: string }) {
	return (
		<Text as="span" fontSize="xs" color="fg.muted" title={fullDate(at)}>
			<span aria-hidden="true">{relativeTime(at)}</span>
			<VisuallyHidden>{fullDate(at)}</VisuallyHidden>
		</Text>
	);
}

export function ChatCard({
	title,
	author,
	at,
	tone = "plain",
	children,
	actions,
}: ChatCardProps) {
	const id = useId();
	return (
		<Box
			as="article"
			aria-labelledby={id}
			px="4"
			py="3"
			rounded="card"
			borderWidth="1px"
			{...TONES[tone]}
		>
			<Flex as="header" align="baseline" gap="2" wrap="wrap" mb="1.5">
				<Heading
					as="h2"
					id={id}
					fontFamily="body"
					fontSize="xs"
					fontWeight="normal"
					textTransform="uppercase"
					letterSpacing="0.08em"
					color="fg.muted"
				>
					{title}
				</Heading>
				{author === undefined ? null : (
					<Text as="span" fontSize="xs" color="fg.muted">
						{author}
					</Text>
				)}
				{at === undefined ? null : <When at={at} />}
			</Flex>
			<Box fontSize="sm" css={{ "& > * + *": { mt: "2" } }}>
				{children}
			</Box>
			{actions === undefined ? null : (
				<Flex mt="3" gap="2" wrap="wrap" align="center">
					{actions}
				</Flex>
			)}
		</Box>
	);
}

/** A card folded to one line (L0): an icon, words, and at most one or two links. */
export function ChatLine({
	icon,
	children,
	actions,
}: {
	icon?: ReactNode;
	children: ReactNode;
	actions?: ReactNode;
}) {
	return (
		<Flex
			align="center"
			gap="2"
			wrap="wrap"
			px="1"
			fontSize="sm"
			color="fg.muted"
			css={{ "& > svg": { flex: "none" } }}
		>
			{icon}
			{children}
			{actions}
		</Flex>
	);
}
