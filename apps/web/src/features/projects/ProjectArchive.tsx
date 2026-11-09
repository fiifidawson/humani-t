/**
 * Archiving a project from its settings (UI_UX_SPEC §6.14, §7.7): owners
 * archive and unarchive; everyone sees whether it is archived.
 *
 * Archiving only hides the project from Home and the default project list.
 * Nothing is deleted and nothing becomes read-only, so it needs no dialog.
 */

import { Badge, Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore } from "lucide-react";
import { unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { fullDate, type Project, shortDate } from "./format";
import { useLeaveForHome } from "./useLeaveForHome";

const WHAT_IT_DOES =
	"Archiving hides the project from Home and the project list. Nothing is deleted, and members can still open it.";

function ArchivedNote({ archivedAt }: { archivedAt: string }) {
	return (
		<Flex as="span" gap="2" align="center" wrap="wrap">
			<Badge variant="outline" colorPalette="gray">
				Archived
			</Badge>
			<span>
				Hidden from Home and the project list since{" "}
				<time dateTime={archivedAt} title={fullDate(archivedAt)}>
					{shortDate(archivedAt)}
				</time>
				. Nothing is deleted.
			</span>
		</Flex>
	);
}

/** Whether the project is archived, in words, for members who can't change it. */
export function ArchiveStatus({ project }: { project: Project }) {
	return project.archived_at === null ? null : (
		<Text fontSize="sm">
			<ArchivedNote archivedAt={project.archived_at} />
		</Text>
	);
}

/** The owner's archive and unarchive control. */
export function ArchiveControl({ project }: { project: Project }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const leaveForHome = useLeaveForHome(project.id);
	const isArchived = project.archived_at !== null;
	const change = useMutation({
		mutationFn: () => {
			const request = { params: { path: { project_id: project.id } } };
			return unwrap(
				isArchived
					? api.POST("/api/v1/projects/{project_id}/unarchive", request)
					: api.POST("/api/v1/projects/{project_id}/archive", request),
			);
		},
		onSuccess: async (changed) => {
			queryClient.setQueryData(keys.project(project.id), changed);
			await queryClient.invalidateQueries({ queryKey: keys.projectLists });
		},
		onError: async (error) => {
			if (isApiError(error, "not-found")) {
				await leaveForHome();
			}
		},
	});

	return (
		<Stack gap="3">
			{change.isError && !isApiError(change.error, "not-found") ? (
				<ErrorNotice error={change.error} />
			) : null}
			<Flex gap="4" align="center" justify="space-between" wrap="wrap">
				{/* Polite, so the new state is read out when the button changes under focus. */}
				<Text fontSize="sm" maxW="md" aria-live="polite">
					{project.archived_at === null ? (
						WHAT_IT_DOES
					) : (
						<ArchivedNote archivedAt={project.archived_at} />
					)}
				</Text>
				{/* One button whose label changes keeps keyboard focus where it was. */}
				<Button
					size="sm"
					variant="outline"
					disabled={change.isPending}
					onClick={() => change.mutate()}
				>
					{isArchived ? <ArchiveRestore /> : <Archive />}
					{isArchived ? "Unarchive project" : "Archive project"}
				</Button>
			</Flex>
		</Stack>
	);
}
