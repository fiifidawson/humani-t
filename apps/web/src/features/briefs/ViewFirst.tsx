/**
 * "Your view first" (UI_UX_SPEC §6.9): before the brief, ask which option the person
 * leans towards. The API decides whether it may be asked and keeps each answer for its
 * author alone; skipping stores nothing, so the question comes back next time.
 */

import { Box, Button, Flex, Heading, Text } from "@chakra-ui/react";
import {
	queryOptions,
	useMutation,
	useQueryClient,
} from "@tanstack/react-query";
import { useId } from "react";
import { type ApiClient, unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { ErrorNotice } from "../../ui/ErrorNotice";

export type Leaning = components["schemas"]["LeaningOut"];

export const leaningQuery = (api: ApiClient, runId: string) =>
	queryOptions({
		queryKey: ["leaning", runId],
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/runs/{run_id}/leaning", {
					params: { path: { run_id: runId } },
				}),
			),
	});

/** The header's line once the person has answered. */
export function leaningWords(leaning: Leaning): string {
	return leaning.option === null
		? "You were not sure before reading."
		: `You leaned towards ${leaning.option} before reading.`;
}

export function ViewFirstQuestion({
	runId,
	options,
	onSkip,
}: {
	runId: string;
	options: readonly string[];
	onSkip: () => void;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const headingId = useId();
	const questionId = useId();
	const key = leaningQuery(api, runId).queryKey;
	const answer = useMutation({
		mutationFn: (option: string | null) =>
			unwrap(
				api.PUT("/api/v1/runs/{run_id}/leaning", {
					params: { path: { run_id: runId } },
					body: { option },
				}),
			),
		onSuccess: (saved) => queryClient.setQueryData(key, saved),
		// Answered elsewhere, or the owner turned the question off: read what is now true.
		onError: (error) =>
			isApiError(error, "conflict")
				? queryClient.invalidateQueries({ queryKey: key })
				: undefined,
	});

	return (
		<Box
			as="section"
			aria-labelledby={headingId}
			p="5"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
			animationStyle="rise-in"
		>
			<Heading as="h3" id={headingId} fontSize="md" fontWeight="semibold">
				Before you read the brief
			</Heading>
			<Text id={questionId} mt="2">
				Which option do you lean towards now?
			</Text>
			{answer.isError ? (
				<Box mt="3">
					<ErrorNotice error={answer.error} />
				</Box>
			) : null}
			<Flex
				role="group"
				aria-labelledby={questionId}
				gap="2"
				wrap="wrap"
				mt="3"
			>
				{[...options, null].map((option) => (
					<Button
						key={option ?? "not-sure"}
						size="sm"
						variant="outline"
						rounded="pill"
						disabled={answer.isPending}
						onClick={() => answer.mutate(option)}
					>
						{option ?? "Not sure"}
					</Button>
				))}
			</Flex>
			<Text mt="3" fontSize="sm" color="fg.muted">
				Only you can see your answer. It is kept with this brief and shown next
				to it.
			</Text>
			<Button size="sm" variant="ghost" mt="2" onClick={onSkip}>
				Skip
			</Button>
		</Box>
	);
}
