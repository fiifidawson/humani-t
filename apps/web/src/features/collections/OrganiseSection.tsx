/**
 * Project settings: the project's collection and tags (UI_UX_SPEC §6.14, §7.3).
 * Owners change them; everyone else sees them.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { FolderInput } from "lucide-react";
import { type FormEvent, useState } from "react";
import { isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { collectionsQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import type { Project } from "../projects/format";
import { MoveToCollection } from "./MoveToCollection";
import { useSetTags } from "./organise";

const splitTags = (typed: string): string[] =>
	typed
		.split(",")
		.map((tag) => tag.trim())
		.filter((tag) => tag !== "");

function TagsForm({ project }: { project: Project }) {
	const saved = project.tags.join(", ");
	const [draft, setDraft] = useState(saved);
	const save = useSetTags(project.id);
	const submit = (event: FormEvent) => {
		event.preventDefault();
		save.mutate(splitTags(draft), {
			onSuccess: (changed) => setDraft(changed.tags.join(", ")),
		});
	};
	const error = isApiError(save.error, "invalid")
		? userMessage(save.error)
		: undefined;
	return (
		<Stack as="form" gap="3" onSubmit={submit}>
			<Field
				label="Tags"
				hint="Separate tags with commas, such as a country, a theme or a method. Only members see them."
				value={draft}
				error={error}
				onChange={(event) => setDraft(event.target.value)}
			/>
			{save.isError && error === undefined ? (
				<ErrorNotice error={save.error} />
			) : null}
			<Flex gap="3" align="center" wrap="wrap">
				<Button
					type="submit"
					size="sm"
					disabled={draft === saved || save.isPending}
				>
					Save tags
				</Button>
				<Text role="status" fontSize="sm" color="fg.muted">
					{save.isSuccess && draft === saved ? "Saved." : ""}
				</Text>
			</Flex>
		</Stack>
	);
}

export function OrganiseSection({ project }: { project: Project }) {
	const api = useApi();
	const collections = useQuery(collectionsQuery(api));
	const [isMoving, setMoving] = useState(false);
	const collection =
		collections.data?.find((c) => c.id === project.collection_id)?.name ??
		"Not in a collection";
	const isOwner = project.role === "owner";

	return (
		<Stack gap="5" fontSize="sm">
			<Flex gap="4" align="center" justify="space-between" wrap="wrap">
				<Text>
					Collection: <strong>{collection}</strong>
				</Text>
				{isOwner ? (
					<Button size="sm" variant="outline" onClick={() => setMoving(true)}>
						<FolderInput />
						Move to collection…
					</Button>
				) : null}
			</Flex>
			{isOwner ? (
				<TagsForm key={project.tags.join("\n")} project={project} />
			) : (
				<Stack gap="1">
					<Text>
						Tags: {project.tags.length === 0 ? "none" : project.tags.join(", ")}
					</Text>
					<Text>Only owners can change the collection and tags.</Text>
				</Stack>
			)}
			{isMoving ? (
				<MoveToCollection
					projectIds={[project.id]}
					current={project.collection_id}
					onClose={() => setMoving(false)}
				/>
			) : null}
		</Stack>
	);
}
