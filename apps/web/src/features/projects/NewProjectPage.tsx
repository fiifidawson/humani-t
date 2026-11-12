/**
 * Create a project by name. The question, mode and options of UI_UX_SPEC §6.3
 * are asked in the workspace that opens next.
 */

import { Button, Stack } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { unwrap } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
export function NewProjectPage() {
	const api = useApi();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [name, setName] = useState("");

	const create = useMutation({
		mutationFn: () => unwrap(api.POST("/api/v1/projects", { body: { name } })),
		onSuccess: async (project) => {
			queryClient.setQueryData(keys.project(project.id), project);
			await queryClient.invalidateQueries({ queryKey: keys.projects });
			await navigate({
				to: "/p/$projectId",
				params: { projectId: project.id },
			});
		},
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		create.mutate();
	};

	const nameError = isApiError(create.error, "invalid")
		? (fieldErrors(create.error)["name"] ?? userMessage(create.error))
		: undefined;
	const otherError =
		create.isError && nameError === undefined ? create.error : null;

	return (
		<Page isNarrow>
			<PageHeading title="New project" />
			<Stack as="form" gap="6" onSubmit={onSubmit}>
				{otherError === null ? null : <ErrorNotice error={otherError} />}
				<Field
					label="Project name"
					hint="Only members of the project can see its name. It is stored encrypted."
					maxLength={200}
					required
					value={name}
					error={nameError}
					onChange={(event) => setName(event.target.value)}
				/>
				<Button
					type="submit"
					alignSelf="flex-start"
					disabled={create.isPending}
				>
					Create project
				</Button>
			</Stack>
		</Page>
	);
}
