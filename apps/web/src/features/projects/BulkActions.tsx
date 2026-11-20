/**
 * Bulk actions on the list view (UI_UX_SPEC §7.2): move to a collection, add a
 * tag, archive. Only the person's own projects can be selected; the API still
 * decides, and says which it skipped.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { Archive, FolderInput, Tag } from "lucide-react";
import { type FormEvent, useState } from "react";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { MoveToCollection } from "../collections/MoveToCollection";
import { useBulkAction } from "../collections/organise";
import { plural } from "../reviews/format";

/** "Moved 3 projects." with, when some were skipped, why. */
export function outcomeMessage(done: string, changed: number, skipped: number) {
	const said = `${done} ${plural(changed, "project")}.`;
	return skipped === 0
		? said
		: `${said} ${plural(skipped, "project was", "projects were")} skipped: only their owners can change them.`;
}

function AddTagDialog({
	projectIds,
	onClose,
	onDone,
}: {
	projectIds: readonly string[];
	onClose: () => void;
	onDone: (message: string) => void;
}) {
	const [tag, setTag] = useState("");
	const add = useBulkAction();
	const submit = (event: FormEvent) => {
		event.preventDefault();
		add.mutate(
			{ action: "add_tag", projectIds, tag },
			{
				onSuccess: (outcome) => {
					onDone(
						outcomeMessage(
							"Tagged",
							outcome.changed.length,
							outcome.skipped.length,
						),
					);
					onClose();
				},
			},
		);
	};
	const error = isApiError(add.error, "invalid")
		? (fieldErrors(add.error)["tag"] ?? userMessage(add.error))
		: undefined;
	return (
		<Dialog
			isOpen
			title={`Add a tag to ${plural(projectIds.length, "project")}`}
			onClose={onClose}
		>
			<Stack as="form" gap="4" fontSize="sm" onSubmit={submit}>
				<Field
					label="Tag"
					hint="Tags already on a project stay. Only the project's members see its tags."
					maxLength={40}
					required
					value={tag}
					error={error}
					onChange={(event) => setTag(event.target.value)}
				/>
				{add.isError && error === undefined ? (
					<ErrorNotice error={add.error} />
				) : null}
				<Flex gap="2" justify="flex-end">
					<Button size="sm" variant="ghost" onClick={onClose}>
						Cancel
					</Button>
					<Button
						type="submit"
						size="sm"
						disabled={tag.trim() === "" || add.isPending}
					>
						Add tag
					</Button>
				</Flex>
			</Stack>
		</Dialog>
	);
}

export function BulkActions({
	selected,
	onDone,
}: {
	selected: readonly string[];
	/** Clears the selection and says what happened. */
	onDone: (message: string) => void;
}) {
	const [open, setOpen] = useState<"move" | "tag" | null>(null);
	const archive = useBulkAction();
	return (
		<Flex
			role="group"
			aria-label="Bulk actions"
			gap="3"
			align="center"
			wrap="wrap"
			p="3"
			rounded="card"
			bg="bg.subtle"
			borderWidth="1px"
			borderColor="border"
		>
			<Text fontSize="sm" fontWeight="medium">
				{selected.length} selected
			</Text>
			<Button size="sm" variant="outline" onClick={() => setOpen("move")}>
				<FolderInput />
				Move to collection…
			</Button>
			<Button size="sm" variant="outline" onClick={() => setOpen("tag")}>
				<Tag />
				Add a tag…
			</Button>
			<Button
				size="sm"
				variant="outline"
				disabled={archive.isPending}
				onClick={() =>
					archive.mutate(
						{ action: "archive", projectIds: selected },
						{
							onSuccess: (outcome) =>
								onDone(
									outcomeMessage(
										"Archived",
										outcome.changed.length,
										outcome.skipped.length,
									),
								),
						},
					)
				}
			>
				<Archive />
				Archive
			</Button>
			{archive.isError ? <ErrorNotice error={archive.error} /> : null}
			{open === "move" ? (
				<MoveToCollection
					projectIds={selected}
					current={null}
					onClose={() => setOpen(null)}
					onMoved={(moved, skipped) =>
						onDone(outcomeMessage("Moved", moved, skipped))
					}
				/>
			) : null}
			{open === "tag" ? (
				<AddTagDialog
					projectIds={selected}
					onClose={() => setOpen(null)}
					onDone={onDone}
				/>
			) : null}
		</Flex>
	);
}
