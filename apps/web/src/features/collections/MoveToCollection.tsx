/**
 * "Move to collection…" (UI_UX_SPEC §7.3): the keyboard way to file projects,
 * from a project's ⋯ menu, its settings, or several at once from the list.
 *
 * A collection's name is seen by everyone in the deployment (ADR 0016), so the
 * form for a new one says so.
 */

import { Button, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { collectionsQuery } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Loading } from "../../ui/Loading";
import { useCreateCollection, useMoveProjects } from "./organise";

const NONE = "";

interface MoveToCollectionProps {
	projectIds: readonly string[];
	/** The collection the project is in now; for several, `null`. */
	current: string | null;
	onClose: () => void;
	/** Told how many projects moved and how many were skipped. */
	onMoved?: (moved: number, skipped: number) => void;
}

export function MoveToCollection({
	projectIds,
	current,
	onClose,
	onMoved,
}: MoveToCollectionProps) {
	const api = useApi();
	const id = useId();
	const collections = useQuery(collectionsQuery(api));
	const [choice, setChoice] = useState<string>(current ?? NONE);
	const [newName, setNewName] = useState("");
	const create = useCreateCollection();
	const move = useMoveProjects();

	const addCollection = (event: FormEvent) => {
		event.preventDefault();
		if (newName.trim() !== "" && !create.isPending) {
			create.mutate(newName, {
				onSuccess: (made) => {
					setChoice(made.id);
					setNewName("");
				},
			});
		}
	};
	const submit = (event: FormEvent) => {
		event.preventDefault();
		move.mutate(
			{ projectIds, collectionId: choice === NONE ? null : choice },
			{
				onSuccess: (outcome) => {
					onMoved?.(outcome.changed.length, outcome.skipped.length);
					onClose();
				},
			},
		);
	};
	const nameError = isApiError(create.error, "invalid", "conflict")
		? (fieldErrors(create.error)["name"] ?? userMessage(create.error))
		: undefined;
	const options = [
		...(collections.data ?? []).map((c) => ({ value: c.id, label: c.name })),
		{ value: NONE, label: "Not in a collection" },
	];

	return (
		<Dialog
			isOpen
			title={
				projectIds.length === 1
					? "Move to collection"
					: `Move ${projectIds.length} projects`
			}
			onClose={onClose}
		>
			<Stack gap="5" fontSize="sm">
				{collections.isPending ? <Loading what="collections" /> : null}
				{collections.isError ? <ErrorNotice error={collections.error} /> : null}
				<form id={`${id}-move`} onSubmit={submit}>
					<Stack
						role="radiogroup"
						aria-labelledby={`${id}-label`}
						gap="2"
						maxH="60"
						overflowY="auto"
					>
						<Text id={`${id}-label`} fontWeight="medium">
							Collection
						</Text>
						{options.map((option) => (
							<Flex key={option.value} gap="2.5" align="center">
								<chakra.input
									type="radio"
									id={`${id}-${option.value || "none"}`}
									name={`${id}-collection`}
									value={option.value}
									checked={choice === option.value}
									onChange={() => setChoice(option.value)}
									boxSize="4"
									accentColor="brand.solid"
									flex="none"
								/>
								<chakra.label htmlFor={`${id}-${option.value || "none"}`}>
									{option.label}
								</chakra.label>
							</Flex>
						))}
					</Stack>
				</form>
				<Stack as="form" gap="2" onSubmit={addCollection}>
					<Field
						label="New collection"
						hint="Everyone in this deployment can see collection names. Use a tag for anything only your team should see."
						maxLength={80}
						value={newName}
						error={nameError}
						onChange={(event) => setNewName(event.target.value)}
					/>
					<Button
						type="submit"
						size="sm"
						variant="outline"
						alignSelf="flex-start"
						disabled={newName.trim() === "" || create.isPending}
					>
						Create collection
					</Button>
				</Stack>
				{move.isError ? <ErrorNotice error={move.error} /> : null}
				<Flex gap="2" justify="flex-end">
					<Button size="sm" variant="ghost" onClick={onClose}>
						Cancel
					</Button>
					<Button
						type="submit"
						form={`${id}-move`}
						size="sm"
						disabled={move.isPending}
					>
						Move
					</Button>
				</Flex>
			</Stack>
		</Dialog>
	);
}
