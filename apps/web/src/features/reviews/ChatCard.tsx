/**
 * The frame every chat card shares (UI_UX_SPEC §5.2, §10.2 `ChatTurn`): a
 * heading that names the card, who made it, when, the body, and at most one
 * main action and two others. One-line forms (L0) use `ChatLine` instead.
 *
 * The tone says who is speaking, so a reader can tell them apart at a glance
 * (UI_UX_SPEC §4.1 "Chat roles"): the person's own words on the right; what
 * needs them with a blue bar; a problem with a purple bar and the word; the
 * rest is Humani-T. Colour is never the only sign: an icon or a word goes too.
 */

import { Box, Flex, Heading, Text, VisuallyHidden } from "@chakra-ui/react";
import { CircleAlert, Hand } from "lucide-react";
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
	plain: { role: "humani-t", bg: "bg.panel", borderColor: "border" },
	person: {
		role: "person",
		bg: "benefit.bg",
		borderColor: "transparent",
		ms: "auto",
		maxW: "85%",
	},
	warning: {
		role: "needs-you",
		bg: "bg.panel",
		borderColor: "border",
		borderStartWidth: "4px",
		borderStartColor: "brand.solid",
	},
	error: {
		role: "problem",
		bg: "bg.panel",
		borderColor: "border",
		borderStartWidth: "4px",
		borderStartColor: "danger.solid",
	},
} as const;

function RoleMark({ tone }: { tone: keyof typeof TONES }) {
	if (tone === "person") {
		return (
			<Text as="span" fontSize="xs" fontWeight="semibold" color="fg">
				You
			</Text>
		);
	}
	if (tone === "warning") {
		return (
			<Box as="span" color="brand.fg" aria-hidden="true" alignSelf="center">
				<Hand size={14} />
			</Box>
		);
	}
	if (tone === "error") {
		return (
			<Flex
				as="span"
				align="center"
				gap="1"
				color="danger.fg"
				fontSize="xs"
				fontWeight="semibold"
				alignSelf="center"
			>
				<CircleAlert size={14} aria-hidden="true" />
				Problem
			</Flex>
		);
	}
	return null;
}

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
	const { role, ...look } = TONES[tone];
	return (
		<Box
			as="article"
			aria-labelledby={id}
			data-role={role}
			px="4"
			py="3"
			rounded="card"
			borderWidth="1px"
			{...look}
		>
			<Flex as="header" align="baseline" gap="2" wrap="wrap" mb="1.5">
				<RoleMark tone={tone} />
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
				// Text-style buttons pin themselves to the top (LinkButton); here every
				// action sits on the row's centre line.
				<Flex
					mt="3"
					gap="2"
					wrap="wrap"
					align="center"
					css={{ "& > *": { alignSelf: "center" } }}
				>
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
			css={{ "& > *": { alignSelf: "center" }, "& > svg": { flex: "none" } }}
		>
			{icon}
			{children}
			{actions}
		</Flex>
	);
}
