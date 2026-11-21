/**
 * New evaluation (UI_UX_SPEC §6.15): a project, a question file, other systems'
 * answers, and a judge the operator configured. The browser only reads the files;
 * the API checks their format and says what is wrong.
 */

import {
	Button,
	chakra,
	Flex,
	NativeSelect,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
	type ChangeEvent,
	type FormEvent,
	type ReactNode,
	useId,
	useState,
} from "react";
import { unwrap } from "../../api/client";
import { useApi } from "../../app/api-context";
import { evaluationKeys, judgeModelsQuery } from "../../app/evaluationQueries";
import { projectListQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { LinkButton } from "../../ui/LinkButton";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";

const MAX_OTHER_SYSTEMS = 2;

interface OtherSystem {
	key: number;
	name: string;
	answersFile: string;
}

/** A labelled control with a hint under it, the ids wired as `Field` wires them. */
function Labelled({
	label,
	hint,
	children,
}: {
	label: string;
	hint?: string;
	children: (ids: { id: string; hintId: string | undefined }) => ReactNode;
}) {
	const id = useId();
	const hintId = hint === undefined ? undefined : `${id}-hint`;
	return (
		<Stack gap="1.5">
			<chakra.label htmlFor={id} fontSize="sm" fontWeight="medium">
				{label}
			</chakra.label>
			{children({ id, hintId })}
			{hint === undefined ? null : (
				<Text id={hintId} fontSize="sm" color="fg.muted">
					{hint}
				</Text>
			)}
		</Stack>
	);
}

async function fileText(event: ChangeEvent<HTMLInputElement>): Promise<string> {
	const file = event.target.files?.[0];
	return file === undefined ? "" : file.text();
}

export function NewEvaluationPage() {
	const api = useApi();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const projects = useQuery(projectListQuery(api));
	const judges = useQuery(judgeModelsQuery(api));
	const [projectId, setProjectId] = useState("");
	const [questionFile, setQuestionFile] = useState("");
	const [systems, setSystems] = useState<OtherSystem[]>([]);
	const [judge, setJudge] = useState<string | null>(null);
	const judgeModel =
		judge ??
		judges.data?.judge_models.find((m) => m !== judges.data?.writer_model) ??
		judges.data?.writer_model ??
		"";

	const start = useMutation({
		mutationFn: () =>
			unwrap(
				api.POST("/api/v1/admin/evaluations", {
					body: {
						project_id: projectId,
						judge_model: judgeModel,
						question_file: questionFile,
						other_systems: systems.map((s) => ({
							name: s.name,
							answers_file: s.answersFile,
						})),
					},
				}),
			),
		onSuccess: async (evaluation) => {
			await queryClient.invalidateQueries({ queryKey: evaluationKeys.all });
			await navigate({
				to: "/admin/evaluations/$evaluationId",
				params: { evaluationId: evaluation.id },
			});
		},
	});

	const change = (key: number, update: Partial<OtherSystem>) =>
		setSystems((all) =>
			all.map((s) => (s.key === key ? { ...s, ...update } : s)),
		);

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		start.mutate();
	};

	if (judges.isError) {
		return (
			<Page isNarrow>
				<PageHeading title="New evaluation" />
				<ErrorNotice
					error={judges.error}
					onRetry={() => void judges.refetch()}
				/>
			</Page>
		);
	}

	return (
		<Page isNarrow>
			<PageHeading title="New evaluation" />
			<Stack as="form" gap="6" onSubmit={onSubmit}>
				{start.isError ? <ErrorNotice error={start.error} /> : null}
				<Labelled
					label="Project"
					hint="humani-t answers from this project's latest review. Only projects you're a member of are listed."
				>
					{({ id, hintId }) => (
						<NativeSelect.Root size="lg">
							<NativeSelect.Field
								id={id}
								aria-describedby={hintId}
								value={projectId}
								onChange={(event) => setProjectId(event.target.value)}
							>
								<option value="">Choose a project</option>
								{(projects.data ?? []).map((project) => (
									<option key={project.id} value={project.id}>
										{project.name}
									</option>
								))}
							</NativeSelect.Field>
							<NativeSelect.Indicator />
						</NativeSelect.Root>
					)}
				</Labelled>
				<Labelled
					label="Question file"
					hint="Plain text, one question per line, up to 50."
				>
					{({ id, hintId }) => (
						<input
							id={id}
							type="file"
							accept=".txt,text/plain"
							aria-describedby={hintId}
							onChange={async (event) => setQuestionFile(await fileText(event))}
						/>
					)}
				</Labelled>
				{systems.map((system, index) => (
					<chakra.fieldset
						key={system.key}
						display="flex"
						flexDirection="column"
						gap="4"
						borderWidth="1px"
						borderColor="border"
						rounded="card"
						p="4"
					>
						<chakra.legend px="1" fontWeight="medium">
							Other system {index + 1}
						</chakra.legend>
						<Field
							label="System name"
							hint="Shown in the results, never to the judge."
							maxLength={60}
							required
							value={system.name}
							onChange={(event) =>
								change(system.key, { name: event.target.value })
							}
						/>
						<Labelled
							label="Answers file"
							hint='JSON Lines: one {"answer": "…"} per line, in the order of the questions.'
						>
							{({ id, hintId }) => (
								<input
									id={id}
									type="file"
									accept=".jsonl,.json,.txt,application/json,text/plain"
									aria-describedby={hintId}
									onChange={async (event) =>
										change(system.key, { answersFile: await fileText(event) })
									}
								/>
							)}
						</Labelled>
						<LinkButton
							type="button"
							onClick={() =>
								setSystems((all) => all.filter((s) => s.key !== system.key))
							}
						>
							Remove this system
						</LinkButton>
					</chakra.fieldset>
				))}
				{systems.length < MAX_OTHER_SYSTEMS ? (
					<Flex direction="column" gap="1">
						<Button
							type="button"
							variant="outline"
							alignSelf="flex-start"
							onClick={() =>
								setSystems((all) => [
									...all,
									{ key: Date.now() + all.length, name: "", answersFile: "" },
								])
							}
						>
							Add another system's answers
						</Button>
						<Text fontSize="sm" color="fg.muted">
							Without them, humani-t answers the questions and nothing is
							judged.
						</Text>
					</Flex>
				) : null}
				<Labelled
					label="Judge model"
					hint="The judge should be from a different model family than the writer."
				>
					{({ id, hintId }) => (
						<NativeSelect.Root size="lg">
							<NativeSelect.Field
								id={id}
								aria-describedby={hintId}
								value={judgeModel}
								onChange={(event) => setJudge(event.target.value)}
							>
								{(judges.data?.judge_models ?? []).map((model) => (
									<option key={model} value={model}>
										{model === judges.data?.writer_model
											? `${model} (also writes the answers)`
											: model}
									</option>
								))}
							</NativeSelect.Field>
							<NativeSelect.Indicator />
						</NativeSelect.Root>
					)}
				</Labelled>
				<Button type="submit" alignSelf="flex-start" disabled={start.isPending}>
					Start evaluation
				</Button>
			</Stack>
		</Page>
	);
}
