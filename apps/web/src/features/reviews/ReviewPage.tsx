/**
 * One review's conversation (UI_UX_SPEC §4.1, §5): the chat with its cards
 * and composer, and the canvas beside it when a canvas route is open.
 *
 * The page loads the review, its plan and its runs, follows the latest run on
 * one stream, and hands all of it to the cards through context. What the
 * composer sends depends on where the review stands (§5.4); the API decides
 * whether that is allowed and answers 409 when it no longer is.
 */

import { Box, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation, useParams } from "@tanstack/react-router";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useId,
	useMemo,
	useState,
} from "react";
import { useApi } from "../../app/api-context";
import { useDraft, usePageMemory } from "../../app/memory";
import { PANEL_LIMITS, usePanelWidth } from "../../app/preferences";
import { projectQuery, sessionQuery } from "../../app/queries";
import {
	planQuery,
	reviewQuery,
	reviewsQuery,
	runsQuery,
} from "../../app/reviewQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { ResizeHandle } from "../../ui/ResizeHandle";
import { useMediaQuery, WIDE_SCREEN } from "../../ui/useMediaQuery";
import { Composer } from "../chat/Composer";
import { CanvasWidthProvider } from "./canvasWidth";
import {
	AskControls,
	FollowUpsProvider,
	SendingQuestion,
	ThreadList,
	useAskSetup,
	useAskState,
	useFollowUpTurns,
	useHasBrief,
} from "./FollowUps";
import { LiveRunProvider } from "./liveRun";
import { totalMasked } from "./masking";
import {
	AcceptedLine,
	AnsweredLine,
	ChangeTurn,
	ClarifyCard,
	DraftCard,
	ErrorCard,
	PastChangeTurn,
	PrivacyReceipt,
	QuestionTurn,
	ReplacedDraftLine,
	StartPlanCard,
} from "./PlanCards";
import { LatestRunCards, PastRunLine } from "./RunCards";
import {
	type Plan,
	type ReviewActions,
	type ReviewContextValue,
	ReviewProvider,
	type Run,
	useReview,
	useReviewActions,
} from "./reviewContext";

const COMPOSER_ID = "composer-input";
const CHANGE_HINT =
	"Say what to change, for example: add 'mobile money' as a synonym for cash.";

interface ComposerSetup {
	label: string;
	placeholder: string;
	note?: string;
	unavailableReason?: string;
	onSend: (text: string) => void;
}

const ignore = () => undefined;

/** What the composer does at this moment (UI_UX_SPEC §5.4). */
function composerFor(
	plan: Plan,
	runs: Run[],
	actions: ReviewActions,
): ComposerSetup {
	const isBusy =
		actions.startPlan.isPending ||
		actions.answer.isPending ||
		actions.change.isPending;
	const busy = isBusy
		? { unavailableReason: "Waiting for the search planner…" }
		: {};
	const questions = plan.questions;
	const open = questions.findIndex((q) => q.answer === null && !q.skipped);
	const question = questions[open];
	const current = plan.current;

	if (plan.status === "new") {
		return {
			label: "Make the search plan",
			placeholder: "Choose Quick or Deep above",
			unavailableReason: isBusy
				? "Waiting for the search planner…"
				: "Make the search plan above first.",
			onSend: ignore,
		};
	}
	if (plan.status === "clarifying" && question !== undefined) {
		return {
			label: `Answering question ${open + 1} of ${questions.length}`,
			placeholder: "Your answer, or pick a suggestion above",
			...busy,
			onSend: (text) =>
				actions.answer.mutate({ questionId: question.id, answer: text }),
		};
	}
	if (current !== null && current.accepted_at === null) {
		const label = `Changing the search · draft ${current.version}`;
		const placeholder = "Say what to change in the search";
		if (plan.pending_change !== null) {
			return {
				label,
				placeholder,
				unavailableReason: "The search planner is drafting the next version.",
				onSend: ignore,
			};
		}
		if (current.is_strict) {
			return {
				label,
				placeholder,
				unavailableReason:
					"Your own terms are used exactly as given, so the model doesn't change them.",
				onSend: ignore,
			};
		}
		return {
			label,
			placeholder,
			note: CHANGE_HINT,
			...busy,
			onSend: (text) =>
				actions.change.mutate({ request: text, basedOn: current.version }),
		};
	}
	// After a plan is accepted, and after a run, a message changes the search: the
	// next draft shows what changed, and accepting it runs from the stage it affects.
	const isRunGoing = runs[0] !== undefined && runs[0].outcome === null;
	const label = "Change the search";
	const placeholder =
		"Say what to change, for example: make it general, any refugees, not only Tigray";
	if (current === null) {
		return {
			label,
			placeholder,
			unavailableReason: "There is no plan yet.",
			onSend: ignore,
		};
	}
	if (isRunGoing) {
		return {
			label,
			placeholder,
			unavailableReason:
				"Wait for the run to finish, or cancel it, to change the search.",
			onSend: ignore,
		};
	}
	if (plan.pending_change !== null) {
		return {
			label,
			placeholder,
			unavailableReason: "The search planner is drafting the next version.",
			onSend: ignore,
		};
	}
	if (current.is_strict) {
		return {
			label,
			placeholder,
			unavailableReason:
				"Your own terms are used exactly as given, so the model doesn't change them.",
			onSend: ignore,
		};
	}
	return {
		label,
		placeholder,
		note: "A change makes a new draft. Nothing runs until you accept it.",
		...busy,
		onSend: (text) =>
			actions.change.mutate({ request: text, basedOn: current.version }),
	};
}

/** The planning conversation in the order it happened, then the runs. */
function Conversation({
	draft,
	isFirstReview,
}: {
	draft: string;
	isFirstReview: boolean;
}) {
	const { plan, runs, actions } = useReview();
	const { startPlan, answer, change } = actions;
	const receipts = plan.receipts;
	const firstReceipt = receipts[0];
	const asked = firstReceipt?.items.find((item) => item.kind === "question");
	const isDeep = plan.mode === "deep";
	// A receipt opens by itself the first time in a project, and whenever
	// something was masked in a deep search (§5.3.2).
	const opensAtFirst = (counts: Record<string, number>, isFirst: boolean) =>
		isFirst || (isDeep && totalMasked(counts) > 0);
	const revisions = receipts.filter((r) => r.purpose === "revise");
	const drafts = receipts.filter((r) => r.purpose === "draft");
	const current = plan.current;
	const pendingChange = change.isPending ? change.variables : undefined;
	const followUps = useFollowUpTurns();

	// Drafts and runs in the order they happened: a draft asked for after Run 1
	// sits below it. Within one draft, its turns keep the order they were added.
	const turns: { at: string; node: ReactNode }[] = [];
	let revision = 0;
	plan.versions.forEach((summary, index) => {
		const next = plan.versions[index + 1];
		const isCurrent = current !== null && current.version === summary.version;
		if (summary.version > 1 && summary.origin === "llm_assisted") {
			const request = isCurrent ? current.change_request : null;
			turns.push({
				at: summary.created_at,
				node:
					request === null ? (
						<PastChangeTurn
							key={`change-${summary.version}`}
							version={summary.version}
						/>
					) : (
						<ChangeTurn
							key={`change-${summary.version}`}
							text={request.text}
							at={request.requested_at}
						/>
					),
			});
			// Receipts carry no version, so the n-th revision's receipt is the n-th
			// "revise" call. A call that failed before drafting would shift this.
			const receipt = revisions[revision];
			const item = receipt?.items.find((i) => i.kind === "change_request");
			revision += 1;
			if (receipt !== undefined && item !== undefined) {
				turns.push({
					at: summary.created_at,
					node: (
						<PrivacyReceipt
							key={`receipt-${receipt.id}`}
							receipt={receipt}
							item={item}
							isOpenAtFirst={opensAtFirst(item.counts, false)}
						/>
					),
				});
			}
		}
		if (next !== undefined) {
			turns.push({
				at: summary.created_at,
				node: (
					<ReplacedDraftLine
						key={`draft-${summary.version}`}
						version={summary.version}
						by={next.version}
					/>
				),
			});
		} else if (isCurrent && current.accepted_at !== null) {
			turns.push({
				at: summary.created_at,
				node: (
					<AcceptedLine key={`draft-${summary.version}`} version={current} />
				),
			});
		} else if (isCurrent) {
			turns.push({
				at: summary.created_at,
				node: <DraftCard key={`draft-${summary.version}`} version={current} />,
			});
		}
	});

	return (
		<>
			<QuestionTurn isSending={startPlan.isPending} />
			{firstReceipt !== undefined && asked !== undefined ? (
				<PrivacyReceipt
					receipt={firstReceipt}
					item={asked}
					isOpenAtFirst={opensAtFirst(asked.counts, isFirstReview)}
				/>
			) : null}
			{startPlan.isError ? (
				<ErrorCard
					title="The search plan couldn't be made"
					error={startPlan.error}
					onRetry={() => {
						if (startPlan.variables !== undefined) {
							startPlan.mutate(startPlan.variables);
						}
					}}
				/>
			) : null}
			{plan.status === "new" && !startPlan.isPending && !startPlan.isError ? (
				<StartPlanCard />
			) : null}
			{plan.questions.length === 0 ? null : plan.status === "clarifying" ? (
				<ClarifyCard draft={draft} />
			) : (
				<AnsweredLine receipts={drafts} />
			)}
			{answer.isError ? (
				<ErrorCard
					title="The answer couldn't be sent"
					error={answer.error}
					onRetry={() => {
						if (answer.variables !== undefined) {
							answer.mutate(answer.variables);
						}
					}}
				/>
			) : null}
			{[
				...turns,
				...runs.slice(1).map((run) => ({
					at: run.created_at,
					node: <PastRunLine key={run.id} run={run} />,
				})),
				...(runs[0] === undefined
					? []
					: [
							{
								at: runs[0].created_at,
								node: <LatestRunCards key="latest-run" />,
							},
						]),
				...followUps,
			]
				.sort((a, b) => a.at.localeCompare(b.at))
				.map((item) => item.node)}
			<SendingQuestion />
			{pendingChange === undefined ? null : (
				<ChangeTurn text={pendingChange.request} isSending />
			)}
			{plan.pending_change === null || pendingChange !== undefined ? null : (
				<>
					<ChangeTurn
						text={plan.pending_change.text}
						at={plan.pending_change.requested_at}
					/>
					<Text px="1" fontSize="sm" color="fg.muted" role="status">
						The search planner is drafting the next version.
					</Text>
				</>
			)}
			{change.isError ? (
				<ErrorCard
					title="The change couldn't be made"
					error={change.error}
					onRetry={() => {
						if (change.variables !== undefined) {
							change.mutate(change.variables);
						}
					}}
				/>
			) : null}
		</>
	);
}

function ReviewLayout({
	draft,
	setDraft,
	isFirstReview,
}: {
	draft: string;
	setDraft: (text: string) => void;
	isFirstReview: boolean;
}) {
	const { review, plan, runs, canManage, actions } = useReview();
	const { pathname } = useLocation();
	const isCanvasOpen = !pathname.replace(/\/$/, "").endsWith(`/r/${review.id}`);
	const hasBrief = useHasBrief();
	const ask = useAskState(draft, setDraft);
	const askSetup = useAskSetup(ask);
	const setup =
		hasBrief && ask.choice === "ask"
			? askSetup
			: composerFor(plan, runs, actions);
	const chatId = useId();
	const [chatWidth, setChatWidth] = usePanelWidth("chat");
	const isWide = useMediaQuery(WIDE_SCREEN);
	const [isFullWidth, setFullWidth] = useState(false);
	const canvasWidth = useMemo(
		() => ({ isFullWidth, setFullWidth }),
		[isFullWidth],
	);
	// Full width means something only while a canvas is open beside the chat.
	const isChatHidden = isFullWidth && isCanvasOpen && isWide;
	useEffect(() => {
		if (!isCanvasOpen) {
			setFullWidth(false);
		}
	}, [isCanvasOpen]);

	return (
		<Flex flex="1" minH="0">
			<Box
				id={chatId}
				display={isChatHidden ? "none" : "flex"}
				minW="0"
				minH="0"
				flex={isCanvasOpen ? { base: "1", lg: `0 0 ${chatWidth}px` } : "1"}
				maxW={isCanvasOpen ? { lg: "calc(100% - 480px)" } : undefined}
			>
				<Flex
					direction="column"
					flex="1"
					minW="0"
					minH="0"
					bg="bg.subtle"
					position="relative"
				>
					<FollowUpsProvider ask={ask}>
						{/* position: relative keeps visually hidden text, which is absolutely
					    positioned, inside this scroller; without it the page itself scrolls. */}
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
							<Stack gap="4" w="full" maxW="3xl" mx="auto">
								<Conversation draft={draft} isFirstReview={isFirstReview} />
							</Stack>
						</Flex>
						<ThreadList />
						{canManage ? (
							<Composer
								label={setup.label}
								placeholder={setup.placeholder}
								draft={draft}
								onDraftChange={setDraft}
								onSend={setup.onSend}
								unavailableReason={setup.unavailableReason}
								note={setup.note}
								above={hasBrief ? <AskControls /> : undefined}
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
								{hasBrief
									? "You can read the answers. Only owners and reviewers can ask."
									: "Only owners and reviewers can change or accept the search plan."}
							</Text>
						)}
					</FollowUpsProvider>
				</Flex>
			</Box>
			{isCanvasOpen && isWide && !isChatHidden ? (
				<ResizeHandle
					label="Resize the conversation"
					controls={chatId}
					edge="end"
					width={chatWidth}
					limits={PANEL_LIMITS.chat}
					onResize={setChatWidth}
				/>
			) : null}
			<CanvasWidthProvider value={canvasWidth}>
				<Outlet />
			</CanvasWidthProvider>
		</Flex>
	);
}

export function ReviewPage() {
	const api = useApi();
	const memory = usePageMemory();
	const { projectId, reviewId } = useParams({
		from: "/signed-in/p/$projectId/r/$reviewId",
	});
	const project = useQuery(projectQuery(api, projectId));
	const review = useQuery(reviewQuery(api, projectId, reviewId));
	const plan = useQuery(planQuery(api, reviewId));
	const runs = useQuery(runsQuery(api, reviewId));
	const reviews = useQuery(reviewsQuery(api, projectId));
	const session = useQuery(sessionQuery(api));
	const actions = useReviewActions(reviewId);
	const [draft, setDraft] = useDraft(`review:${reviewId}`);
	const focusComposer = useCallback(
		() => document.getElementById(COMPOSER_ID)?.focus(),
		[],
	);

	// A review created from the workspace carries its mode here; start its plan
	// once, now that this page can show the question and any failure.
	const startPlan = actions.startPlan.mutate;
	useEffect(() => {
		const mode = memory.planModes.get(reviewId);
		if (mode !== undefined && plan.data?.status === "new") {
			memory.planModes.delete(reviewId);
			startPlan(mode);
		}
	}, [memory, reviewId, plan.data?.status, startPlan]);

	const value = useMemo<ReviewContextValue | null>(
		() =>
			project.data === undefined ||
			review.data === undefined ||
			plan.data === undefined ||
			runs.data === undefined
				? null
				: {
						project: project.data,
						review: review.data,
						plan: plan.data,
						runs: runs.data,
						canManage: project.data.role !== "viewer",
						userId: session.data?.user.id ?? null,
						actions,
						setComposerText: setDraft,
						focusComposer,
					},
		[
			project.data,
			review.data,
			plan.data,
			runs.data,
			session.data,
			actions,
			setDraft,
			focusComposer,
		],
	);

	const failed = [review, plan, runs, reviews].find((query) => query.isError);
	if (failed !== undefined) {
		return (
			<Stack p="6" gap="4" maxW="3xl" mx="auto" w="full">
				<ErrorNotice
					error={failed.error}
					onRetry={() => void failed.refetch()}
				/>
			</Stack>
		);
	}
	if (value === null || reviews.data === undefined) {
		return (
			<Stack p="6" maxW="3xl" mx="auto" w="full">
				<Loading what="the review" />
			</Stack>
		);
	}
	const latest = value.runs[0];
	const oldest = reviews.data.at(-1);
	const layout = (
		<ReviewLayout
			draft={draft}
			setDraft={setDraft}
			isFirstReview={oldest?.id === reviewId}
		/>
	);
	return (
		<ReviewProvider value={value}>
			{latest === undefined ? (
				layout
			) : (
				<LiveRunProvider key={latest.id} run={latest}>
					{layout}
				</LiveRunProvider>
			)}
		</ReviewProvider>
	);
}
