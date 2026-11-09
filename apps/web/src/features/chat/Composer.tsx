/**
 * The one text box under the chat (UI_UX_SPEC §5.4).
 *
 * Controlled: the draft lives with the caller, so unsent text survives the
 * component going away (a route change, a sign-in). The label says what
 * sending will do; Enter sends, Shift+Enter adds a line.
 *
 * The box grows with its text, wrapped lines included, up to 40% of the
 * viewport, then scrolls. A hidden copy of the text in the same grid cell sets
 * the height. Chakra's `autoresize` was not used: it measures in script and
 * writes `style.height`, which src/csp.test.ts keeps out of application code.
 */

import {
	Box,
	chakra,
	Flex,
	IconButton,
	Text,
	VisuallyHidden,
} from "@chakra-ui/react";
import { ArrowUp } from "lucide-react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import { useId } from "react";

interface ComposerProps {
	label: string;
	placeholder: string;
	draft: string;
	onDraftChange: (draft: string) => void;
	onSend: (text: string) => void;
	/** Set when sending is not possible yet; the text is kept and the reason shown. */
	unavailableReason?: string | undefined;
	/** A line of help for this moment, read with the box ("Say what to change, for example…"). */
	note?: string | undefined;
	/** Shown above the label, such as a choice of what the box does. */
	above?: ReactNode;
}

/** The textarea and its hidden copy must lay text out identically, or the height drifts. */
const sharedText = {
	gridArea: "1 / 1 / 2 / 2",
	font: "inherit",
	lineHeight: "1.5rem",
	py: "1",
	overflowWrap: "anywhere",
	maxH: "40vh",
} as const;

export function Composer({
	label,
	placeholder,
	draft,
	onDraftChange,
	onSend,
	unavailableReason,
	note,
	above,
}: ComposerProps) {
	const id = useId();
	const hintId = `${id}-hint`;
	const reasonId = `${id}-reason`;
	const noteId = `${id}-note`;
	const describedBy = [
		unavailableReason === undefined ? null : reasonId,
		note === undefined ? null : noteId,
		hintId,
	]
		.filter((part) => part !== null)
		.join(" ");
	const text = draft.trim();
	const canSend = text !== "" && unavailableReason === undefined;

	const send = () => {
		if (canSend) {
			onSend(text);
			onDraftChange("");
		}
	};

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		send();
	};

	const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		// isComposing: Enter also confirms a word in Japanese, Chinese or
		// Korean input methods, and must not send half a sentence.
		if (
			event.key === "Enter" &&
			!event.shiftKey &&
			!event.nativeEvent.isComposing
		) {
			event.preventDefault();
			send();
		}
	};

	return (
		<chakra.form
			flex="none"
			px="4"
			pt="3"
			pb="4"
			borderTopWidth="1px"
			borderColor="border"
			bg="bg"
			onSubmit={onSubmit}
		>
			<Box maxW="3xl" mx="auto">
				{above}
				<chakra.label
					htmlFor="composer-input"
					display="block"
					px="1"
					pb="1.5"
					fontSize="xs"
					textTransform="uppercase"
					letterSpacing="0.08em"
					color="fg.muted"
				>
					{label}
				</chakra.label>
				<Flex
					align="flex-end"
					gap="2"
					pl="4"
					pr="1.5"
					py="1.5"
					rounded="card"
					borderWidth="1px"
					borderColor="border.emphasized"
					bg="bg.panel"
					_focusWithin={{
						borderColor: "focusRing",
						boxShadow: "0 0 0 1px var(--ht-colors-focus-ring)",
					}}
				>
					<Box
						data-value={draft}
						flex="1"
						minW="0"
						display="grid"
						_after={{
							...sharedText,
							content: "attr(data-value) ' '",
							visibility: "hidden",
							whiteSpace: "pre-wrap",
						}}
					>
						<chakra.textarea
							id="composer-input"
							rows={1}
							value={draft}
							placeholder={placeholder}
							aria-describedby={describedBy}
							onChange={(event) => onDraftChange(event.target.value)}
							onKeyDown={onKeyDown}
							{...sharedText}
							border="0"
							outline="0"
							resize="none"
							bg="transparent"
							color="fg"
							overflowY="auto"
							_placeholder={{ color: "fg.muted" }}
						/>
					</Box>
					<IconButton
						type="submit"
						size="sm"
						rounded="pill"
						disabled={!canSend}
					>
						<ArrowUp />
						<VisuallyHidden>Send</VisuallyHidden>
					</IconButton>
				</Flex>
				{unavailableReason === undefined ? null : (
					<Text id={reasonId} px="1" pt="2" fontSize="xs" color="caveat.fg">
						{unavailableReason}
					</Text>
				)}
				{note === undefined ? null : (
					<Text id={noteId} px="1" pt="2" fontSize="xs" color="fg.muted">
						{note}
					</Text>
				)}
				<Text id={hintId} px="1" pt="1.5" fontSize="xs" color="fg.muted">
					Enter to send · Shift+Enter for a new line
				</Text>
			</Box>
		</chakra.form>
	);
}
