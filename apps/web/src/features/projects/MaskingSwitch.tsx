/**
 * Project settings, Privacy: whether names, places and identifiers are masked before
 * text reaches the model (ADR 0014). The API says what the switch holds, what applies
 * now and whether it may be turned off; this only shows it and sends the owner's choice.
 */

import { Button, Flex, Stack, Switch, Text } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { unwrap } from "../../api/client";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { ReauthCancelledError, useReauth } from "../auth/ReauthDialog";
import type { Project } from "./format";

const LABEL =
	"Mask names, places and identifiers before text reaches the model";
const REQUIRED =
	"This deployment sends text to a hosted model, so masking stays on.";

function EffectiveState({ project }: { project: Project }) {
	return (
		<Text aria-live="polite">
			{project.is_masking_effective
				? "Masking is on: what is found is replaced with placeholders such as [PERSON_1] before the model reads it."
				: "Masking is off: the model reads this project's questions, papers and documents as written. The masking rules below still apply."}
		</Text>
	);
}

export function MaskingSwitch({ project }: { project: Project }) {
	return (
		<Stack gap="2" fontSize="sm" mb="5">
			{project.role === "owner" ? (
				<OwnerSwitch project={project} />
			) : (
				<>
					<EffectiveState project={project} />
					<Text>Only owners can change this.</Text>
				</>
			)}
			{project.can_turn_masking_off ? null : <Text>{REQUIRED}</Text>}
		</Stack>
	);
}

function OwnerSwitch({ project }: { project: Project }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const withReauth = useReauth();
	const [isConfirming, setConfirming] = useState(false);
	const change = useMutation({
		mutationFn: (isOn: boolean) =>
			withReauth("Turning masking off needs you to sign in again.", () =>
				unwrap(
					api.PUT("/api/v1/projects/{project_id}/masking", {
						params: { path: { project_id: project.id } },
						body: { is_masking_on: isOn },
					}),
				),
			),
		onSuccess: async (changed) => {
			setConfirming(false);
			queryClient.setQueryData(keys.project(project.id), changed);
			await queryClient.invalidateQueries({ queryKey: keys.projectLists });
		},
	});
	const isLocked = project.is_masking_on && !project.can_turn_masking_off;
	const failure =
		change.isError && !(change.error instanceof ReauthCancelledError)
			? change.error
			: null;

	return (
		<>
			{failure === null || isConfirming ? null : (
				<ErrorNotice error={failure} />
			)}
			{/* Remounted as the dialog opens and closes: the hidden checkbox keeps the
			    click's state otherwise, though turning off is not confirmed yet. */}
			<Switch.Root
				key={isConfirming ? "confirming" : "settled"}
				checked={project.is_masking_on}
				disabled={isLocked || change.isPending}
				onCheckedChange={(details) =>
					details.checked ? change.mutate(true) : setConfirming(true)
				}
			>
				<Switch.HiddenInput />
				<Switch.Control>
					<Switch.Thumb />
				</Switch.Control>
				<Switch.Label>{LABEL}</Switch.Label>
			</Switch.Root>
			<EffectiveState project={project} />
			<Dialog
				isOpen={isConfirming}
				title="Turn masking off?"
				onClose={() => setConfirming(false)}
			>
				<Stack gap="4" fontSize="sm">
					<Text>
						Names, places, email addresses and other identifiers in this
						project's questions, papers and documents will reach the model as
						written. Only the masking rules still apply. You can turn masking
						back on at any time.
					</Text>
					{failure === null ? null : <ErrorNotice error={failure} />}
					<Flex gap="2" justify="flex-end">
						<Button
							size="sm"
							variant="ghost"
							onClick={() => setConfirming(false)}
						>
							Cancel
						</Button>
						<Button
							size="sm"
							colorPalette="danger"
							disabled={change.isPending}
							onClick={() => change.mutate(false)}
						>
							Turn masking off
						</Button>
					</Flex>
				</Stack>
			</Dialog>
		</>
	);
}
