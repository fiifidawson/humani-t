/**
 * The keyboard shortcuts (UI_UX_SPEC §11.5), shown with `?`. What each key does
 * is in useShortcuts.ts and the review page; this is the list people read.
 */

import { Link as ChakraLink, Kbd, Stack, Table, Text } from "@chakra-ui/react";
import { Link } from "@tanstack/react-router";
import { Fragment } from "react";
import { Dialog } from "../../ui/Dialog";

const SHORTCUTS: readonly { keys: readonly string[]; does: string }[] = [
	{ keys: ["Ctrl K", "⌘K"], does: "Search and commands" },
	{ keys: ["["], does: "Fold or unfold the sidebar" },
	{ keys: ["]"], does: "Open or close the canvas" },
	{ keys: ["Esc"], does: "Close the source panel, then the canvas" },
	{ keys: ["g", "h"], does: "Go home" },
	{ keys: ["g", "p"], does: "Go to projects" },
	{ keys: ["/"], does: "Write in the chat box" },
	{ keys: ["j", "k"], does: "Next or previous card in the chat" },
	{ keys: ["Enter"], does: "On a card: open it" },
	{ keys: ["?"], does: "Show shortcuts" },
];

const JOINERS: Readonly<Record<string, string>> = {
	"Ctrl K": " or ",
	g: " then ",
	j: " or ",
};

export function ShortcutsDialog({
	isOpen,
	onClose,
}: {
	isOpen: boolean;
	onClose: () => void;
}) {
	return (
		<Dialog isOpen={isOpen} title="Keyboard shortcuts" onClose={onClose}>
			<Stack gap="4">
				<Table.Root size="sm">
					<Table.Header>
						<Table.Row>
							<Table.ColumnHeader>Keys</Table.ColumnHeader>
							<Table.ColumnHeader>What they do</Table.ColumnHeader>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{SHORTCUTS.map((shortcut) => (
							<Table.Row key={shortcut.does}>
								<Table.Cell whiteSpace="nowrap">
									{shortcut.keys.map((key, n) => (
										<Fragment key={key}>
											{n === 0 ? null : JOINERS[shortcut.keys[0] ?? ""]}
											<Kbd>{key}</Kbd>
										</Fragment>
									))}
								</Table.Cell>
								<Table.Cell>{shortcut.does}</Table.Cell>
							</Table.Row>
						))}
					</Table.Body>
				</Table.Root>
				<Text fontSize="sm" color="fg.muted">
					Keys on their own do nothing while you type. You can turn them off in{" "}
					<ChakraLink asChild color="inherit" textDecoration="underline">
						<Link to="/settings" onClick={onClose}>
							Account
						</Link>
					</ChakraLink>
					.
				</Text>
			</Stack>
		</Dialog>
	);
}
