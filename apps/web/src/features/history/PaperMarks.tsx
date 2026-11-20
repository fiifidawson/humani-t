/**
 * Seen and saved marks on papers and briefs (UI_UX_SPEC §7.5). Opening a
 * paper, a passage or a brief tells the API; the API says what is seen, and
 * where else. Marks change at once and roll back if the API refuses (§11.3).
 */

import { Link as ChakraLink, Flex, IconButton, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { Eye, Star } from "lucide-react";
import { Fragment, useEffect } from "react";
import { unwrapEmpty } from "../../api/client";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { historyKeys, paperMarksQuery } from "../../app/historyQueries";
import { linkStyle } from "../../ui/LinkButton";
import { plural } from "../reviews/format";

type Marks = components["schemas"]["ProjectPaperMarksOut"];
type PaperMarks = components["schemas"]["PaperMarksOut"];

const blank = (paperId: string): PaperMarks => ({
	paper_id: paperId,
	is_seen: false,
	is_saved: false,
	seen_in_other_projects: [],
});

function useMarkMutation(
	projectId: string,
	paperId: string,
	change: (marks: PaperMarks) => PaperMarks,
	send: () => Promise<void>,
) {
	const queryClient = useQueryClient();
	const key = historyKeys.paperMarks(projectId);
	return useMutation({
		mutationFn: send,
		onMutate: async () => {
			await queryClient.cancelQueries({ queryKey: key });
			const before = queryClient.getQueryData<Marks>(key);
			if (before !== undefined) {
				const found = before.papers.find((p) => p.paper_id === paperId);
				const others = before.papers.filter((p) => p.paper_id !== paperId);
				queryClient.setQueryData<Marks>(key, {
					papers: [...others, change(found ?? blank(paperId))],
				});
			}
			return { before };
		},
		onError: (_error, _input, context) => {
			if (context?.before !== undefined) {
				queryClient.setQueryData(key, context.before);
			}
		},
		onSettled: async () => {
			await queryClient.invalidateQueries({ queryKey: key });
			await queryClient.invalidateQueries({ queryKey: historyKeys.saved });
		},
	});
}

/** A function that marks the paper seen; call it when the person opens it. */
export function useMarkSeen(projectId: string, paperId: string) {
	const api = useApi();
	const path = {
		params: { path: { project_id: projectId, paper_id: paperId } },
	};
	const seen = useMarkMutation(
		projectId,
		paperId,
		(marks) => ({ ...marks, is_seen: true }),
		() =>
			unwrapEmpty(
				api.PUT("/api/v1/projects/{project_id}/papers/{paper_id}/seen", path),
			),
	);
	return seen.mutate;
}

/** Mark the source panel's paper seen once it has loaded; documents have no mark. */
export function useSeenOnOpen(paperId: string | null) {
	const { projectId } = useParams({ strict: false });
	const markSeen = useMarkSeen(projectId ?? "", paperId ?? "");
	useEffect(() => {
		if (projectId !== undefined && paperId !== null) {
			markSeen();
		}
	}, [projectId, paperId, markSeen]);
}

/** Mark a brief read once it is shown, so Needs you stops listing it. */
export function useBriefSeen(briefId: string | undefined) {
	const api = useApi();
	const queryClient = useQueryClient();
	const seen = useMutation({
		mutationFn: (id: string) =>
			unwrapEmpty(
				api.PUT("/api/v1/briefs/{brief_id}/seen", {
					params: { path: { brief_id: id } },
				}),
			),
		onSettled: () =>
			queryClient.invalidateQueries({ queryKey: historyKeys.needsYou }),
	});
	const { mutate } = seen;
	useEffect(() => {
		if (briefId !== undefined) {
			mutate(briefId);
		}
	}, [briefId, mutate]);
}

/** The marks on one paper of a project, from one read per project. */
export function usePaperMarks(projectId: string, paperId: string): PaperMarks {
	const api = useApi();
	const marks = useQuery(paperMarksQuery(api, projectId));
	return (
		marks.data?.papers.find((p) => p.paper_id === paperId) ?? blank(paperId)
	);
}

/** ☆ Save: adds the paper to the person's saved list, or takes it off. */
export function SaveButton({
	projectId,
	paperId,
	title,
}: {
	projectId: string;
	paperId: string;
	title: string;
}) {
	const api = useApi();
	const marks = usePaperMarks(projectId, paperId);
	const path = {
		params: { path: { project_id: projectId, paper_id: paperId } },
	};
	const route = "/api/v1/projects/{project_id}/papers/{paper_id}/saved";
	const save = useMarkMutation(
		projectId,
		paperId,
		(current) => ({ ...current, is_saved: !marks.is_saved }),
		() =>
			unwrapEmpty(
				marks.is_saved ? api.DELETE(route, path) : api.PUT(route, path),
			),
	);
	return (
		<IconButton
			variant="ghost"
			size="2xs"
			aria-label={`Save: ${title}`}
			aria-pressed={marks.is_saved}
			title={marks.is_saved ? "Saved" : "Save"}
			onClick={() => save.mutate()}
			css={{ "& svg": { fill: marks.is_saved ? "currentColor" : "none" } }}
		>
			<Star />
		</IconButton>
	);
}

/** "Seen", and the other projects the person saw it in, as links. */
export function SeenMark({
	projectId,
	paperId,
}: {
	projectId: string;
	paperId: string;
}) {
	const marks = usePaperMarks(projectId, paperId);
	if (!marks.is_seen) {
		return null;
	}
	const others = marks.seen_in_other_projects;
	return (
		<Flex
			as="span"
			gap="1.5"
			align="center"
			wrap="wrap"
			fontSize="xs"
			color="fg.muted"
		>
			<Eye size={12} aria-hidden="true" />
			<Text as="span">Seen</Text>
			{others.length === 0 ? null : (
				<Text as="span">
					· in {plural(others.length, "other project")}:{" "}
					{others.map((project, index) => (
						<Fragment key={project.id}>
							{index === 0 ? null : ", "}
							<ChakraLink asChild css={linkStyle}>
								<Link to="/p/$projectId" params={{ projectId: project.id }}>
									{project.name}
								</Link>
							</ChakraLink>
						</Fragment>
					))}
				</Text>
			)}
		</Flex>
	);
}
