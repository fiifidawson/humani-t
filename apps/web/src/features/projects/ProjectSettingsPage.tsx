/**
 * Project settings (UI_UX_SPEC §6.14). For now the danger zone alone: members
 * and settings join it later in Wave 2.
 *
 * Deleting destroys the project's key (crypto-shredding), so it asks for the
 * project's name typed out. The API decides who may delete and whether the
 * sign-in is recent enough; this page only reflects it.
 */

import { Box, Button, Flex, Heading, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { unwrapEmpty } from "../../api/client";
import { useApi } from "../../app/api-context";
import { keys, projectQuery } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Loading } from "../../ui/Loading";
import { ReauthCancelledError, useReauth } from "../auth/ReauthDialog";

export function ProjectSettingsPage() {
	const api = useApi();
	const { projectId } = useParams({ from: "/signed-in/p/$projectId/settings" });
	const project = useQuery(projectQuery(api, projectId));
	const [isDeleting, setDeleting] = useState(false);
	const headingId = useId();

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
				<Box
					as="section"
					aria-labelledby={headingId}
					rounded="card"
					borderWidth="1px"
					borderColor="border.error"
					px="5"
					py="4"
				>
					<Heading
						id={headingId}
						as="h2"
						fontSize="lg"
						fontWeight="semibold"
						mb="2"
					>
						Danger zone
					</Heading>
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
				</Box>
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
