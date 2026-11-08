/**
 * A project's workspace (UI_UX_SPEC §4.1): the project's header over either
 * its latest review (features/reviews/ReviewPage) or, when it has none, the
 * empty chat where a review starts (§6.3, §5.4).
 *
 * Starting is two requests: the review, then its plan. The plan waits on the
 * model, so this page creates the review and hands over to the review's page,
 * which shows the question while the plan is made and offers a retry if it
 * fails. The mode travels in page memory, never in the URL or storage.
 */

import {
	Badge,
	Box,
	Link as ChakraLink,
	Flex,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Link,
	Navigate,
	Outlet,
	useNavigate,
	useParams,
} from "@tanstack/react-router";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { useDraft, usePageMemory } from "../../app/memory";
import { keys, projectQuery } from "../../app/queries";
import { reviewsQuery } from "../../app/reviewQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";

import { Composer } from "../chat/Composer";
import { ChatCard } from "../reviews/ChatCard";
import { modeLabel, type PlanMode } from "../reviews/format";
import { ModeChoice } from "../reviews/ModeChoice";
import { type Project, roleLabel } from "./format";

const QUESTION_PROMPT = "What decision do you need evidence for?";

function StartReview({ project }: { project: Project }) {
	const api = useApi();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const memory = usePageMemory();
	const [draft, setDraft] = useDraft(`project:${project.id}`);
	const [mode, setMode] = useState<PlanMode>("deep");
	const canStartReview = project.role !== "viewer";

	const create = useMutation({
		mutationFn: (input: { question: string; mode: PlanMode }) =>
			unwrap(
				api.POST("/api/v1/projects/{project_id}/reviews", {
					params: { path: { project_id: project.id } },
					body: { question: input.question },
				}),
			),
		onSuccess: async (review, input) => {
			memory.planModes.set(review.id, input.mode);
			queryClient.setQueryData(keys.review(review.id), review);
			await queryClient.invalidateQueries({
				queryKey: keys.reviews(project.id),
			});
			await navigate({
				to: "/p/$projectId/r/$reviewId",
				params: { projectId: project.id, reviewId: review.id },
			});
		},
		// Nothing was created: give the text back, so nothing typed is lost.
		onError: (_, input) => setDraft(input.question),
	});
	const sending = create.isPending ? create.variables : undefined;

	return (
		<>
			<Flex
				role="log"
				aria-label="Project conversation"
				flex="1"
				minH="0"
				position="relative"
				overflowY="auto"
				direction="column"
				p="4"
			>
				{sending === undefined ? (
					<Stack gap="6" m="auto" maxW="xl" w="full">
						<Stack gap="2" textAlign="center">
							<Text
								fontFamily="heading"
								fontWeight="semibold"
								fontSize={{ base: "2xl", md: "3xl" }}
								lineHeight="1.2"
							>
								{QUESTION_PROMPT}
							</Text>
							<Text
								fontSize="sm"
								color="fg.muted"
								display="inline-flex"
								gap="1.5"
								alignItems="center"
								justifyContent="center"
							>
								<ShieldCheck size={16} />
								Personal details are masked before anything is sent to the
								model.
							</Text>
						</Stack>
						{canStartReview ? (
							<Box
								p="4"
								rounded="card"
								borderWidth="1px"
								borderColor="border"
								bg="bg.panel"
							>
								<ModeChoice value={mode} onChange={setMode} />
							</Box>
						) : null}
						{create.isError ? <ErrorNotice error={create.error} /> : null}
					</Stack>
				) : (
					<Stack gap="4" w="full" maxW="3xl" mx="auto">
						<ChatCard title="Question" tone="person">
							<Text whiteSpace="pre-wrap" overflowWrap="anywhere" fontSize="md">
								{sending.question}
							</Text>
							<Text color="fg.muted" fontSize="xs">
								{modeLabel(sending.mode)}
							</Text>
							<Text role="status" color="fg.muted" fontSize="xs">
								Sending…
							</Text>
						</ChatCard>
					</Stack>
				)}
			</Flex>
			{canStartReview ? (
				<Composer
					label="Start a review"
					placeholder={QUESTION_PROMPT}
					draft={draft}
					onDraftChange={setDraft}
					onSend={(question) => create.mutate({ question, mode })}
					unavailableReason={create.isPending ? "Sending…" : undefined}
				/>
			) : (
				<Text
					px="4"
					py="3"
					borderTopWidth="1px"
					borderColor="border"
					bg="bg"
					fontSize="sm"
					color="fg.muted"
					textAlign="center"
				>
					Only owners and reviewers can start a review.
				</Text>
			)}
		</>
	);
}

/** The project's own page: its latest review if it has one, else where to start one. */
export function ProjectHome() {
	const api = useApi();
	const { projectId } = useParams({ from: "/signed-in/p/$projectId/" });
	const project = useQuery(projectQuery(api, projectId));
	const reviews = useQuery(reviewsQuery(api, projectId));

	if (project.data === undefined || reviews.isPending) {
		return (
			<Box p="6">
				<Loading what="the project's reviews" />
			</Box>
		);
	}
	if (reviews.isError) {
		return (
			<Box p="6">
				<ErrorNotice
					error={reviews.error}
					onRetry={() => void reviews.refetch()}
				/>
			</Box>
		);
	}
	// One review per project in this version (§3.1); the newest is first.
	const latest = reviews.data[0];
	if (latest !== undefined) {
		return (
			<Navigate
				to="/p/$projectId/r/$reviewId"
				params={{ projectId, reviewId: latest.id }}
				replace
			/>
		);
	}
	return (
		<Flex direction="column" flex="1" minH="0" bg="bg.subtle">
			<StartReview project={project.data} />
		</Flex>
	);
}

export function WorkspacePage() {
	const api = useApi();
	const { projectId } = useParams({ from: "/signed-in/p/$projectId" });
	const project = useQuery(projectQuery(api, projectId));
	// Fetched here, alongside the project, so the project's page draws once
	// with or without its reviews; ProjectHome reads them from the cache.
	const reviews = useQuery(reviewsQuery(api, projectId));

	if (project.isPending || (project.isSuccess && reviews.isPending)) {
		return (
			<Page>
				<Loading what="the project" />
			</Page>
		);
	}
	if (project.isError) {
		return (
			<Page>
				<PageHeading
					title={
						isApiError(project.error, "not-found")
							? "Project not found"
							: "Something went wrong"
					}
				/>
				<ErrorNotice
					error={project.error}
					onRetry={() => void project.refetch()}
				/>
				<ChakraLink asChild css={linkStyle} alignSelf="flex-start">
					<Link to="/">Go home</Link>
				</ChakraLink>
			</Page>
		);
	}
	return (
		<Flex
			as="section"
			aria-labelledby="workspace-title"
			direction="column"
			flex="1"
			minH="0"
		>
			<Flex
				as="header"
				flex="none"
				align="center"
				gap="3"
				px={{ base: "4", md: "6" }}
				py="3"
				borderBottomWidth="1px"
				borderColor="border"
				bg="bg"
			>
				<Box flex="1" minW="0">
					<PageHeading
						id="workspace-title"
						title={project.data.name}
						size="workspace"
					/>
				</Box>
				<Badge variant="outline" colorPalette="gray" flex="none">
					{roleLabel(project.data.role)}
				</Badge>
			</Flex>
			<Outlet />
		</Flex>
	);
}
