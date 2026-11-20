/**
 * Saved papers (UI_UX_SPEC §7.5), reached from the rail: the papers this
 * person saved with ☆, from projects they still belong to.
 */

import {
	Button,
	Link as ChakraLink,
	Flex,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { unwrapEmpty } from "../../api/client";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { historyKeys, savedPapersQuery } from "../../app/historyQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { fullDate, shortDate } from "../projects/format";

type SavedPaper = components["schemas"]["SavedPaperOut"];

function SavedRow({ paper }: { paper: SavedPaper }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const unsave = useMutation({
		mutationFn: () =>
			unwrapEmpty(
				api.DELETE("/api/v1/projects/{project_id}/papers/{paper_id}/saved", {
					params: {
						path: { project_id: paper.project.id, paper_id: paper.paper_id },
					},
				}),
			),
		onSettled: async () => {
			await queryClient.invalidateQueries({ queryKey: historyKeys.saved });
			await queryClient.invalidateQueries({
				queryKey: historyKeys.paperMarks(paper.project.id),
			});
		},
	});
	return (
		<Stack as="li" gap="1" py="3" borderBottomWidth="1px" borderColor="border">
			<Text fontWeight="semibold" overflowWrap="anywhere">
				{paper.link === null ? (
					paper.title
				) : (
					<ChakraLink
						href={paper.link}
						target="_blank"
						rel="noopener noreferrer"
						css={linkStyle}
					>
						{paper.title}
					</ChakraLink>
				)}
			</Text>
			<Flex gap="3" wrap="wrap" align="center" color="fg.muted" fontSize="sm">
				<Text as="span">
					{[paper.year, paper.doi === null ? null : `DOI ${paper.doi}`]
						.filter((part) => part !== null)
						.join(" · ")}
				</Text>
				<ChakraLink asChild css={linkStyle}>
					<Link
						to="/p/$projectId/r/$reviewId/papers"
						params={{ projectId: paper.project.id, reviewId: paper.review_id }}
						search={{}}
					>
						{paper.project.name}
					</Link>
				</ChakraLink>
				<Text as="span">
					saved{" "}
					<time dateTime={paper.saved_at} title={fullDate(paper.saved_at)}>
						{shortDate(paper.saved_at)}
					</time>
				</Text>
				<Button
					size="xs"
					variant="ghost"
					ms="auto"
					aria-label={`Remove from saved: ${paper.title}`}
					disabled={unsave.isPending}
					onClick={() => unsave.mutate()}
				>
					Remove
				</Button>
			</Flex>
			{unsave.isError ? <ErrorNotice error={unsave.error} /> : null}
		</Stack>
	);
}

export function SavedPage() {
	const api = useApi();
	const saved = useQuery(savedPapersQuery(api));
	return (
		<Page>
			<PageHeading title="Saved papers" />
			{saved.isPending ? <Loading what="your saved papers" /> : null}
			{saved.isError ? (
				<ErrorNotice error={saved.error} onRetry={() => void saved.refetch()} />
			) : null}
			{saved.isSuccess && saved.data.papers.length === 0 ? (
				<Text>
					No saved papers yet. Save a paper with the star in a review's list of
					papers.
				</Text>
			) : null}
			{saved.isSuccess && saved.data.papers.length > 0 ? (
				<Stack as="ul" listStyle="none" m="0" p="0" gap="0">
					{saved.data.papers.map((paper) => (
						<SavedRow key={paper.paper_id} paper={paper} />
					))}
				</Stack>
			) : null}
		</Page>
	);
}
