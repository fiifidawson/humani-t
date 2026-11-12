/**
 * One follow-up thread on its own page (UI_UX_SPEC §6.12), with a box that
 * asks in it. The thread names its review; its questions are then read from
 * the review's threads, which asking and polling already keep current.
 */

import { Box, Link as ChakraLink, Flex, Stack } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { useApi } from "../../app/api-context";
import { useDraft } from "../../app/memory";
import { threadQuery } from "../../app/reviewQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Composer } from "../chat/Composer";
import {
	type AskState,
	FollowUpsProvider,
	SendingQuestion,
	useAskSetup,
	useFollowUpTurns,
	VIEWER_ASK_NOTE,
	ViewerNote,
} from "./FollowUps";
import { ReviewProvider, useReview, useReviewValue } from "./reviewContext";

const ignore = () => undefined;

/** The box here only asks, and always in this thread. */
const THREAD_ASK: AskState = {
	choice: "ask",
	choose: ignore,
	isNewThread: false,
	setNewThread: ignore,
	changeSearch: null,
};

function Notice({ children }: { children: ReactNode }) {
	return (
		<Stack p="6" gap="4" maxW="3xl" mx="auto" w="full">
			{children}
		</Stack>
	);
}

export function ThreadPage() {
	const api = useApi();
	const { projectId, threadId } = useParams({
		from: "/signed-in/p/$projectId/ask/$threadId",
	});
	const thread = useQuery(threadQuery(api, threadId));
	if (thread.isError) {
		return (
			<Notice>
				<ErrorNotice
					error={thread.error}
					onRetry={() => void thread.refetch()}
				/>
			</Notice>
		);
	}
	if (thread.data === undefined) {
		return (
			<Notice>
				<Loading what="the thread" />
			</Notice>
		);
	}
	return (
		<ThreadOfReview
			projectId={projectId}
			reviewId={thread.data.review_id}
			threadId={threadId}
		/>
	);
}

function ThreadOfReview({
	projectId,
	reviewId,
	threadId,
}: {
	projectId: string;
	reviewId: string;
	threadId: string;
}) {
	const [draft, setDraft] = useDraft(`thread:${threadId}`);
	const { value, failed } = useReviewValue(projectId, reviewId, setDraft);
	if (failed !== undefined) {
		return (
			<Notice>
				<ErrorNotice
					error={failed.error}
					onRetry={() => void failed.refetch()}
				/>
			</Notice>
		);
	}
	if (value === null) {
		return (
			<Notice>
				<Loading what="the thread" />
			</Notice>
		);
	}
	return (
		<ReviewProvider value={value}>
			<Thread threadId={threadId} draft={draft} setDraft={setDraft} />
		</ReviewProvider>
	);
}

function Thread({
	threadId,
	draft,
	setDraft,
}: {
	threadId: string;
	draft: string;
	setDraft: (text: string) => void;
}) {
	const { project, review, canManage } = useReview();
	const setup = useAskSetup(THREAD_ASK, threadId);
	const turns = useFollowUpTurns(threadId);
	return (
		<Flex direction="column" flex="1" minH="0" bg="bg.subtle">
			<FollowUpsProvider ask={THREAD_ASK}>
				<Box px="4" py="2" borderBottomWidth="1px" borderColor="border" bg="bg">
					<Box maxW="3xl" mx="auto">
						<ChakraLink asChild css={linkStyle} fontSize="sm">
							<Link
								to="/p/$projectId/r/$reviewId"
								params={{ projectId: project.id, reviewId: review.id }}
							>
								<ArrowLeft size={16} aria-hidden="true" />
								Back to the review
							</Link>
						</ChakraLink>
					</Box>
				</Box>
				<Flex
					role="log"
					aria-label="Thread"
					flex="1"
					minH="0"
					position="relative"
					overflowY="auto"
					direction="column"
					p="4"
				>
					<Stack gap="4" w="full" maxW="3xl" mx="auto">
						{turns.map((turn) => turn.node)}
						<SendingQuestion />
					</Stack>
				</Flex>
				{canManage ? (
					<Composer
						label={setup.label}
						placeholder={setup.placeholder}
						draft={draft}
						onDraftChange={setDraft}
						onSend={setup.onSend}
						unavailableReason={setup.unavailableReason}
						note={setup.note}
					/>
				) : (
					<ViewerNote>{VIEWER_ASK_NOTE}</ViewerNote>
				)}
			</FollowUpsProvider>
		</Flex>
	);
}
