/**
 * Project settings, Briefs: "Your view first" (UI_UX_SPEC §6.9, §16 Q3). When on, the
 * brief first asks each member which option they lean towards. Off by default; the
 * API decides who may switch it, and this only shows it and sends the owner's choice.
 */

import { Stack, Switch, Text } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { unwrap } from "../../api/client";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import type { Project } from "./format";

const LABEL =
	"Ask each member which option they lean towards before showing the brief";
const MEANING =
	"They can skip it every time. Only they see their answer, next to the brief.";

export function ViewFirstSwitch({ project }: { project: Project }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const change = useMutation({
		mutationFn: (isOn: boolean) =>
			unwrap(
				api.PUT("/api/v1/projects/{project_id}/view-first", {
					params: { path: { project_id: project.id } },
					body: { is_view_first_on: isOn },
				}),
			),
		onSuccess: async (changed) => {
			queryClient.setQueryData(keys.project(project.id), changed);
			await queryClient.invalidateQueries({ queryKey: keys.projectLists });
		},
	});

	if (project.role !== "owner") {
		return (
			<Stack gap="1" fontSize="sm">
				<Text>
					{project.is_view_first_on
						? "Each member is asked which option they lean towards before the brief."
						: "Members are not asked which option they lean towards before the brief."}
				</Text>
				<Text>Only owners can change this.</Text>
			</Stack>
		);
	}
	return (
		<Stack gap="2" fontSize="sm">
			{change.isError ? <ErrorNotice error={change.error} /> : null}
			<Switch.Root
				checked={project.is_view_first_on}
				disabled={change.isPending}
				onCheckedChange={(details) => change.mutate(details.checked)}
			>
				<Switch.HiddenInput />
				<Switch.Control>
					<Switch.Thumb />
				</Switch.Control>
				<Switch.Label>{LABEL}</Switch.Label>
			</Switch.Root>
			<Text color="fg.muted">{MEANING}</Text>
		</Stack>
	);
}
