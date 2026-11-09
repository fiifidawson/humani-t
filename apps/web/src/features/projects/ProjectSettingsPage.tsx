/**
 * Project settings (UI_UX_SPEC §6.14): the name, the members and the danger
 * zone. Archiving, tags and leaving the project are not here yet.
 *
 * Deleting destroys the project's key (crypto-shredding), so it asks for the
 * project's name typed out. The API decides who may rename, change members or
 * delete, and whether the sign-in is recent enough; this page only reflects it.
 */

import { Box, Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { unwrap, unwrapEmpty } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { keys, projectQuery } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Loading } from "../../ui/Loading";
import { ReauthCancelledError, useReauth } from "../auth/ReauthDialog";
import { MembersSection } from "./ProjectMembers";
import { SettingsSection } from "./SettingsSection";

export function ProjectSettingsPage() {
	const api = useApi();
	const { projectId } = useParams({ from: "/signed-in/p/$projectId/settings" });
	const project = useQuery(projectQuery(api, projectId));
	const [isDeleting, setDeleting] = useState(false);

	if (project.isError) {
		return <ErrorNotice error={project.error} />;
	}
	if (project.data === undefined) {
		return <Loading what="the project" />;
	}
	const { name, role } = project.data;
	return (
		<Box flex="1" minH="0" overflowY="auto" px={{ base: "4", md: "6" }} py="6">
			<Stack gap="6" maxW="2xl">
				<SettingsSection title="General">
					{role === "owner" ? (
						<RenameForm projectId={projectId} name={name} />
					) : (
						<Stack gap="1" fontSize="sm">
							<Text fontWeight="medium">{name}</Text>
							<Text>Only owners can rename the project.</Text>
						</Stack>
					)}
				</SettingsSection>
				<SettingsSection title="Members">
					<MembersSection projectId={projectId} isOwner={role === "owner"} />
				</SettingsSection>
				<SettingsSection title="Danger zone" isDanger>
					{role === "owner" ? (
						<Flex gap="4" align="center" justify="space-between" wrap="wrap">
							<Text fontSize="sm" maxW="md">
								Delete this project and everything in it. This can't be undone.
							</Text>
							<Button
								size="sm"
								variant="outline"
								colorPalette="danger"
								onClick={() => setDeleting(true)}
							>
								<Trash2 />
								Delete project…
							</Button>
						</Flex>
					) : (
						<Text fontSize="sm">Only owners can delete a project.</Text>
					)}
				</SettingsSection>
			</Stack>
			{isDeleting ? (
				<DeleteDialog
					projectId={projectId}
					name={name}
					onClose={() => setDeleting(false)}
				/>
			) : null}
		</Box>
	);
}

function RenameForm({ projectId, name }: { projectId: string; name: string }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const [draft, setDraft] = useState(name);
	const rename = useMutation({
		mutationFn: () =>
			unwrap(
				api.PATCH("/api/v1/projects/{project_id}", {
					params: { path: { project_id: projectId } },
					body: { name: draft },
				}),
			),
		onSuccess: async (project) => {
			setDraft(project.name);
			queryClient.setQueryData(keys.project(projectId), project);
			await queryClient.invalidateQueries({ queryKey: keys.projects });
		},
	});
	const nameError = isApiError(rename.error, "invalid")
		? (fieldErrors(rename.error)["name"] ?? userMessage(rename.error))
		: undefined;
	const otherError =
		rename.isError && nameError === undefined ? rename.error : null;
	const submit = (event: FormEvent) => {
		event.preventDefault();
		if (!rename.isPending) {
			rename.mutate();
		}
	};

	return (
		<Stack as="form" gap="3" onSubmit={submit}>
			{otherError === null ? null : <ErrorNotice error={otherError} />}
			<Field
				label="Project name"
				hint="Only members can see it. It is stored encrypted."
				maxLength={200}
				required
				value={draft}
				error={nameError}
				onChange={(event) => setDraft(event.target.value)}
			/>
			<Flex gap="3" align="center" wrap="wrap">
				<Button
					type="submit"
					size="sm"
					disabled={draft === name || rename.isPending}
				>
					Save name
				</Button>
				<Text role="status" fontSize="sm" color="fg.muted">
					{rename.isSuccess && draft === name ? "Saved." : ""}
				</Text>
			</Flex>
		</Stack>
	);
}

function DeleteDialog({
	projectId,
	name,
	onClose,
}: {
	projectId: string;
	name: string;
	onClose: () => void;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const navigate = useNavigate();
	const withReauth = useReauth();
	const [typed, setTyped] = useState("");
	const remove = useMutation({
		mutationFn: () =>
			withReauth("Deleting a project needs you to sign in again.", () =>
				unwrapEmpty(
					api.DELETE("/api/v1/projects/{project_id}", {
						params: { path: { project_id: projectId } },
					}),
				),
			),
		onSuccess: async () => {
			queryClient.removeQueries({ queryKey: keys.project(projectId) });
			await queryClient.invalidateQueries({ queryKey: keys.projects });
			await navigate({ to: "/" });
		},
	});
	// The exact name, as on GitHub: deleting is irreversible, and typing it is a pause.
	const isTyped = typed === name;
	const submit = (event: FormEvent) => {
		event.preventDefault();
		if (isTyped && !remove.isPending) {
			remove.mutate();
		}
	};

	return (
		<Dialog isOpen title={`Delete ${name}?`} onClose={onClose}>
			<form onSubmit={submit}>
				<Stack gap="4" fontSize="sm">
					<Text>
						Its questions, search plans, papers, screening decisions, documents
						and briefs become unreadable for good. Backups follow once they
						expire. This can't be undone.
					</Text>
					<Field
						label="Type the project's name to confirm"
						hint={name}
						value={typed}
						autoComplete="off"
						onChange={(event) => setTyped(event.target.value)}
					/>
					{remove.isError && !(remove.error instanceof ReauthCancelledError) ? (
						<ErrorNotice error={remove.error} />
					) : null}
					<Flex gap="2" justify="flex-end">
						<Button size="sm" variant="ghost" onClick={onClose}>
							Cancel
						</Button>
						<Button
							type="submit"
							size="sm"
							colorPalette="danger"
							disabled={!isTyped || remove.isPending}
						>
							{remove.isPending ? "Deleting…" : "Delete project"}
						</Button>
					</Flex>
				</Stack>
			</form>
		</Dialog>
	);
}
