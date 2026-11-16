/**
 * Project settings, Instructions for the model (UI_UX_SPEC §6.14, ADR 0015): one
 * text for judging papers, one for writing briefs and answers. The API adds them
 * after the model's own instructions and masks them before sending. Owners save a
 * new version; everyone else reads them.
 */

import {
	Box,
	Button,
	chakra,
	Flex,
	Stack,
	Text,
	Textarea,
} from "@chakra-ui/react";
import {
	queryOptions,
	useMutation,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { type ApiClient, unwrap } from "../../api/client";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { fullDate, shortDate } from "./format";
import { SettingsSection } from "./SettingsSection";

type Instructions = components["schemas"]["ModelInstructionsOut"];
type Version = components["schemas"]["ModelInstructionsVersionOut"];

// The API's limit, shown so people see it before the API refuses a longer text.
const MAX_LENGTH = 2000;
const NUMBER = new Intl.NumberFormat("en-GB");

const BOXES = [
	{
		key: "screening",
		label: "How to judge papers",
		hint: "Added to every screening call.",
		example: "Treat studies in refugee camps as displaced settings.",
	},
	{
		key: "writing",
		label: "How to write",
		hint: "Added when the brief and answers to follow-up questions are written.",
		example: "Write for a field manager: short sentences, no jargon.",
	},
] as const;

type Texts = Record<(typeof BOXES)[number]["key"], string>;

const instructionsQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: keys.modelInstructions(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/model-instructions", {
					params: { path: { project_id: projectId } },
				}),
			),
	});

export function ModelInstructionsSection({
	projectId,
	isOwner,
}: {
	projectId: string;
	isOwner: boolean;
}) {
	const api = useApi();
	const instructions = useQuery(instructionsQuery(api, projectId));
	return (
		<SettingsSection title="Instructions for the model">
			<Stack gap="4" fontSize="sm">
				<Text>
					Added after the model's own instructions, never in place of them.
					Masked like your questions before they are sent. A run uses the
					version saved when it started.
				</Text>
				{instructions.isPending ? (
					<Loading what="the instructions" />
				) : instructions.isError ? (
					<ErrorNotice
						error={instructions.error}
						onRetry={() => void instructions.refetch()}
					/>
				) : isOwner ? (
					<InstructionsForm
						projectId={projectId}
						current={instructions.data.current}
					/>
				) : (
					<ReadOnly current={instructions.data.current} />
				)}
			</Stack>
		</SettingsSection>
	);
}

function SavedLine({ version }: { version: Version | null }) {
	if (version === null) {
		return <Text color="fg.muted">Not saved yet.</Text>;
	}
	return (
		<Text color="fg.muted">
			Version {version.version}, saved by {version.saved_by.display_name} on{" "}
			<time dateTime={version.saved_at} title={fullDate(version.saved_at)}>
				{shortDate(version.saved_at)}
			</time>
		</Text>
	);
}

function ReadOnly({ current }: { current: Version | null }) {
	return (
		<Stack gap="3">
			{BOXES.map((box) => (
				<Stack key={box.key} gap="1">
					<Text fontWeight="medium">{box.label}</Text>
					<Text whiteSpace="pre-wrap" overflowWrap="anywhere">
						{current === null || current[box.key] === ""
							? "None."
							: current[box.key]}
					</Text>
				</Stack>
			))}
			<SavedLine version={current} />
			<Text>Only owners can change the instructions.</Text>
		</Stack>
	);
}

function InstructionsBox({
	label,
	hint,
	example,
	value,
	onChange,
}: {
	label: string;
	hint: string;
	example: string;
	value: string;
	onChange: (value: string) => void;
}) {
	const id = useId();
	return (
		<Stack gap="1.5">
			<chakra.label htmlFor={id} fontWeight="medium">
				{label}
			</chakra.label>
			<Textarea
				id={id}
				rows={4}
				maxLength={MAX_LENGTH}
				placeholder={example}
				value={value}
				aria-describedby={`${id}-hint ${id}-count`}
				onChange={(event) => onChange(event.target.value)}
			/>
			<Flex gap="3" justify="space-between" color="fg.muted">
				<Text id={`${id}-hint`}>{hint}</Text>
				<Text id={`${id}-count`} flex="none">
					{NUMBER.format(value.length)} of {NUMBER.format(MAX_LENGTH)}{" "}
					characters
				</Text>
			</Flex>
		</Stack>
	);
}

function InstructionsForm({
	projectId,
	current,
}: {
	projectId: string;
	current: Version | null;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const saved: Texts = {
		screening: current?.screening ?? "",
		writing: current?.writing ?? "",
	};
	const [texts, setTexts] = useState<Texts>(saved);
	// Not optimistic: the version line changes only once the API has stored it.
	const save = useMutation({
		mutationFn: () =>
			unwrap(
				api.PUT("/api/v1/projects/{project_id}/model-instructions", {
					params: { path: { project_id: projectId } },
					body: { ...texts, based_on_version: current?.version ?? null },
				}),
			),
		onSuccess: (version) => {
			setTexts({ screening: version.screening, writing: version.writing });
			queryClient.setQueryData<Instructions>(
				keys.modelInstructions(projectId),
				(old) => ({
					current: version,
					versions: [...(old?.versions ?? []), version],
				}),
			);
		},
	});
	const isChanged =
		texts.screening !== saved.screening || texts.writing !== saved.writing;
	const submit = (event: FormEvent) => {
		event.preventDefault();
		if (isChanged && !save.isPending) {
			save.mutate();
		}
	};

	return (
		<Stack as="form" gap="4" onSubmit={submit}>
			{BOXES.map((box) => (
				<InstructionsBox
					key={box.key}
					label={box.label}
					hint={box.hint}
					example={box.example}
					value={texts[box.key]}
					onChange={(value) => setTexts({ ...texts, [box.key]: value })}
				/>
			))}
			{save.isError ? <ErrorNotice error={save.error} /> : null}
			<Flex gap="3" align="center" wrap="wrap">
				<Button type="submit" size="sm" disabled={!isChanged || save.isPending}>
					{save.isPending ? "Saving…" : "Save instructions"}
				</Button>
				<Box role="status">
					<SavedLine version={current} />
				</Box>
			</Flex>
		</Stack>
	);
}
