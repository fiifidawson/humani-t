/**
 * Follow-up questions (UI_UX_SPEC §5.3.9, §6.12): the box's choice, each
 * question with its answer, the threads, and the source panel for citations.
 * An open question is polled until answered or failed. Who may ask is the
 * API's rule; this only hides what the caller's role cannot do.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
	MessageCircleQuestion,
	MessageSquarePlus,
	ShieldCheck,
	SlidersHorizontal,
	TriangleAlert,
} from "lucide-react";
import {
	createContext,
	type ReactNode,
	useCallback,
	useContext,
	useEffect,
	useId,
	useMemo,
	useRef,
	useState,
} from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { briefQuery } from "../../app/briefQueries";
import { keys } from "../../app/queries";
import {
	paperQuery,
	questionQuery,
	threadsQuery,
} from "../../app/reviewQueries";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { linkStyle } from "../../ui/LinkButton";
import {
	type ChipSource,
	type Claim,
	Claims,
	type OnOpenSource,
} from "../briefs/Claim";
import { Passages, SourcePanelFrame } from "../briefs/SourcePanel";
import { PendingReply } from "../chat/PendingReply";
import { ChatCard, When } from "./ChatCard";
import { plural } from "./format";
import { MaskedText } from "./MaskedText";
import { totalMasked } from "./masking";
import { ErrorCard } from "./PlanCards";
import {
	type FollowUp,
	type ReviewActions,
	type Thread,
	useReview,
	withQuestion,
} from "./reviewContext";

type AnswerSource = components["schemas"]["AnswerSourceOut"];

export const ASK_LABEL = "Ask about the evidence";
const CHANGE_LABEL = "Change the search";
const NEW_THREAD_LABEL = "New thread";
const NOTHING_FOUND = "Nothing in this project answers that.";
const PREVIEW_WORDS = 60;
export const VIEWER_ASK_NOTE =
	"You can read the answers. Only owners and reviewers can ask.";

export type ComposerChoice = "ask" | "change";

const isOpen = (question: FollowUp) =>
	question.status === "pending" || question.status === "answering";

/** Where the list of threads jumps to: the thread's first question. */
const threadAnchor = (threadId: string) => `thread-${threadId}`;

/** What the box is set to do, shared by the box, the answers and the thread controls. */
export interface AskState {
	choice: ComposerChoice;
	choose: (choice: ComposerChoice) => void;
	/** Whether the next question starts a thread rather than continuing the latest. */
	isNewThread: boolean;
	setNewThread: (isNewThread: boolean) => void;
	/**
	 * Switch the box to changing the search, starting from `text` unless
	 * something is typed; null where the box cannot change the search.
	 */
	changeSearch: ((text: string) => void) | null;
}

export function useAskState(
	draft: string,
	setDraft: (text: string) => void,
): AskState {
	const { focusComposer } = useReview();
	const [choice, choose] = useState<ComposerChoice>("ask");
	const [isNewThread, setNewThread] = useState(false);
	const isDraftEmpty = draft.trim() === "";
	return useMemo(
		() => ({
			choice,
			choose,
			isNewThread,
			setNewThread,
			changeSearch: (text: string) => {
				choose("change");
				if (isDraftEmpty) {
					setDraft(text);
				}
				focusComposer();
			},
		}),
		[choice, isNewThread, isDraftEmpty, setDraft, focusComposer],
	);
}

const AskContext = createContext<AskState | null>(null);

function useAsk(): AskState {
	const state = useContext(AskContext);
	if (state === null) {
		throw new Error("useAsk must be used inside <FollowUpsProvider>");
	}
	return state;
}

/** What the follow-up cards and controls share: the box's state and the source panel. */
export function FollowUpsProvider({
	ask,
	children,
}: {
	ask: AskState;
	children: ReactNode;
}) {
	return (
		<AskContext value={ask}>
			<AnswerSourceProvider>{children}</AnswerSourceProvider>
		</AskContext>
	);
}

/** Whether the review's latest run wrote a decision brief, which is what questions are about. */
export function useHasBrief(): boolean {
	const api = useApi();
	const { review, runs } = useReview();
	const latest = runs[0];
	const brief = useQuery({
		...briefQuery(api, review.id, latest?.id),
		enabled: latest !== undefined && latest.outcome !== null,
	});
	return latest !== undefined && brief.data !== undefined;
}

/**
 * What the box does while asking (§5.4 "A brief exists"): it continues
 * `threadId`, or else the latest thread, or starts one after New thread.
 */
export function useAskSetup(state: AskState, threadId?: string) {
	const api = useApi();
	const { review, actions } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const { ask } = actions;
	const { isNewThread, setNewThread } = state;
	const continued =
		threadId === undefined
			? threads.data?.[0]
			: threads.data?.find((thread) => thread.id === threadId);
	const last = continued?.questions.at(-1);
	// The API answers one question at a time per thread, so a new thread need not wait.
	const isWaiting = !isNewThread && last !== undefined && isOpen(last);
	const reason = threads.isError
		? "The questions so far couldn't be loaded. Reload the page to try again."
		: threads.data === undefined
			? "Loading the questions so far…"
			: ask.isPending || isWaiting
				? "Wait for the answer to your last question."
				: null;
	return {
		label: ASK_LABEL,
		placeholder: isNewThread
			? "Ask a question to start a new thread"
			: "Ask a follow-up",
		note: "Answers use only the papers and documents in this project.",
		...(reason === null ? {} : { unavailableReason: reason }),
		onSend: (text: string) => {
			setNewThread(false);
			ask.mutate({
				question: text,
				threadId: isNewThread ? null : (threadId ?? continued?.id ?? null),
			});
		},
	};
}

/** Two pressed-or-not buttons, not a menu: both choices stay in sight. */
function ComposerChoiceSwitch({
	choice,
	onChoose,
}: {
	choice: ComposerChoice;
	onChoose: (choice: ComposerChoice) => void;
}) {
	const option = (value: ComposerChoice, icon: ReactNode, label: string) => (
		<Button
			type="button"
			size="xs"
			variant={choice === value ? "solid" : "outline"}
			aria-pressed={choice === value}
			onClick={() => onChoose(value)}
		>
			{icon}
			{label}
		</Button>
	);
	return (
		<Flex role="group" aria-label="What the box does" gap="2" wrap="wrap">
			{option("ask", <MessageCircleQuestion />, ASK_LABEL)}
			{option("change", <SlidersHorizontal />, CHANGE_LABEL)}
		</Flex>
	);
}

/**
 * Above the box once a brief exists: what the box does, and, while asking
 * and once a thread exists, New thread. Pressed, the next question starts a
 * thread; pressed again, it continues the latest one.
 */
export function AskControls() {
	const api = useApi();
	const { review } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const { choice, choose, isNewThread, setNewThread } = useAsk();
	const hasThread = (threads.data?.length ?? 0) > 0;
	return (
		<Flex gap="2" wrap="wrap" align="center" justify="space-between" pb="2.5">
			<ComposerChoiceSwitch choice={choice} onChoose={choose} />
			{choice === "ask" && hasThread ? (
				<Button
					type="button"
					size="xs"
					variant="outline"
					aria-pressed={isNewThread}
					_pressed={{ bg: "bg.emphasized", borderColor: "fg.muted" }}
					onClick={() => setNewThread(!isNewThread)}
				>
					<MessageSquarePlus />
					{NEW_THREAD_LABEL}
				</Button>
			) : null}
		</Flex>
	);
}

/** Under the chat in place of the box, for a viewer. */
export function ViewerNote({ children }: { children: ReactNode }) {
	return (
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
			{children}
		</Text>
	);
}

/**
 * The review's threads, newest first, folded away under the conversation.
 * Choosing one jumps to its first question in the chat; Open goes to its page.
 */
export function ThreadList() {
	const api = useApi();
	const { project, review } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const [isOpenList, setOpenList] = useState(false);
	const id = useId();
	const all = threads.data ?? [];
	if (all.length === 0) {
		return null;
	}
	const jump = (threadId: string) => {
		const target = document.getElementById(threadAnchor(threadId));
		target?.scrollIntoView?.({ block: "start" });
		target?.focus({ preventScroll: true });
	};
	return (
		<Box
			flex="none"
			px="4"
			py="1"
			borderTopWidth="1px"
			borderColor="border"
			bg="bg"
		>
			<Box maxW="3xl" mx="auto">
				<DisclosureButton
					isOpen={isOpenList}
					controls={`${id}-threads`}
					onToggle={() => setOpenList(!isOpenList)}
				>
					{plural(all.length, "thread")}
				</DisclosureButton>
				{isOpenList ? (
					<chakra.ul
						id={`${id}-threads`}
						aria-label="Threads"
						listStyleType="none"
						maxH="48"
						overflowY="auto"
						pb="2"
					>
						{all.map((thread) => (
							<Flex as="li" key={thread.id} align="center" gap="2">
								<chakra.button
									type="button"
									display="block"
									flex="1"
									minW="0"
									textAlign="start"
									px="2"
									py="1.5"
									rounded="card"
									fontSize="sm"
									cursor="pointer"
									_hover={{ bg: "bg.muted" }}
									onClick={() => jump(thread.id)}
								>
									<Text as="span" display="block" truncate>
										<MaskedText
											text={thread.questions[0]?.question_sent ?? ""}
											isInline
										/>
									</Text>
									<Text
										as="span"
										display="block"
										fontSize="xs"
										color="fg.muted"
									>
										{plural(thread.questions.length, "question")} ·{" "}
										<When at={thread.created_at} />
									</Text>
								</chakra.button>
								<ChakraLink asChild css={linkStyle} fontSize="sm" px="2">
									<Link
										to="/p/$projectId/ask/$threadId"
										params={{ projectId: project.id, threadId: thread.id }}
									>
										Open
									</Link>
								</ChakraLink>
							</Flex>
						))}
					</chakra.ul>
				) : null}
			</Box>
		</Box>
	);
}

interface OpenSource {
	tag: string;
	source: AnswerSource | undefined;
	runId: string | null;
	/** The chip pressed: focus goes back to it when the panel closes. */
	chip: HTMLElement;
}

const AnswerSourceContext = createContext<(open: OpenSource) => void>(
	() => undefined,
);

/**
 * Holds the source panel for answers' citations. An answer carries its own
 * passages, which may be ones the brief never cited, so the panel shows those
 * rather than the brief's evidence.
 */
function AnswerSourceProvider({ children }: { children: ReactNode }) {
	const [open, setOpen] = useState<OpenSource | null>(null);
	const chip = useRef<HTMLElement | null>(null);
	const show = useCallback((next: OpenSource) => {
		chip.current = next.chip;
		setOpen(next);
	}, []);
	const close = useCallback(() => {
		setOpen(null);
		chip.current?.focus();
	}, []);
	return (
		<AnswerSourceContext value={show}>
			{children}
			{open === null ? null : (
				<SourcePanelFrame key={open.tag} tag={open.tag} onClose={close}>
					{open.source === undefined ? (
						<Text
							color="caveat.fg"
							display="flex"
							gap="1.5"
							alignItems="center"
						>
							<TriangleAlert size={16} />
							This citation has no passage. Report it.
						</Text>
					) : (
						<Stack gap="4">
							{open.source.source_kind === "document" ? (
								<Text>One of your project's documents.</Text>
							) : open.runId === null ? (
								<Text>A paper in this review.</Text>
							) : (
								<PaperDetails
									runId={open.runId}
									paperId={open.source.paper_id}
								/>
							)}
							<Passages passages={open.source.passages} />
						</Stack>
					)}
				</SourcePanelFrame>
			)}
		</AnswerSourceContext>
	);
}

/** A cited paper's title and year, from the run the answer drew on. */
function PaperDetails({ runId, paperId }: { runId: string; paperId: string }) {
	const api = useApi();
	const { review } = useReview();
	const paper = useQuery(paperQuery(api, review.id, paperId, runId));
	if (paper.data === undefined) {
		return (
			<Text>
				A paper in this review.
				{paper.isError ? " Its title couldn't be loaded." : null}
			</Text>
		);
	}
	const { title, year } = paper.data.paper;
	return (
		<Box>
			<Text fontWeight="semibold">{title}</Text>
			{year === null ? null : <Text color="fg.muted">{year}</Text>}
		</Box>
	);
}

/**
 * Each question with its answer, stamped with when it was asked, for the
 * conversation to place among the drafts and runs; only `threadId`'s when
 * given. Every thread but the oldest opens with a "New thread" divider.
 */
export function useFollowUpTurns(
	threadId?: string,
): { at: string; node: ReactNode }[] {
	const api = useApi();
	const { review, canManage, actions } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const all = (threads.data ?? []).filter(
		(thread) => threadId === undefined || thread.id === threadId,
	);
	const lastAsked = all[0]?.questions.at(-1);
	const oldest = all.at(-1);
	const canRetry = canManage && !actions.ask.isPending;
	return all.flatMap((thread) =>
		thread.questions.map((question, index) => ({
			at: question.asked_at,
			node: (
				<FollowUpTurn
					key={`question-${question.id}`}
					question={question}
					startsThread={
						index > 0 ? null : thread.id === oldest?.id ? "first" : "later"
					}
					canRetry={canRetry && question.id === lastAsked?.id}
				/>
			),
		})),
	);
}

/** A question on its way to the API (§11.3): the person's turn at once, then any failure. */
export function SendingQuestion() {
	const { actions } = useReview();
	const { ask } = actions;
	if (ask.variables === undefined || ask.isSuccess || ask.isIdle) {
		return null;
	}
	return (
		<>
			<ChatCard title="Follow-up question" tone="person">
				<Text whiteSpace="pre-wrap" overflowWrap="anywhere">
					{ask.variables.question}
				</Text>
			</ChatCard>
			{ask.isPending ? <PendingReply doing="Sending your question" /> : null}
			{ask.isError ? (
				<ErrorCard
					title="Your question couldn't be sent"
					error={ask.error}
					onRetry={() => {
						if (ask.variables !== undefined) {
							ask.mutate(ask.variables);
						}
					}}
				/>
			) : null}
		</>
	);
}

/**
 * The words to ask again or start a search from. The masked text is what the
 * model saw before; the words typed in this visit are better still, since
 * masking runs on them again.
 */
function askedWords(question: FollowUp, ask: ReviewActions["ask"]): string {
	return ask.data?.id === question.id && ask.variables !== undefined
		? ask.variables.question
		: question.question_sent;
}

/** A quiet rule where a thread begins, so a reader can tell which questions belong together. */
function ThreadDivider() {
	return (
		<Flex
			role="separator"
			aria-label={NEW_THREAD_LABEL}
			align="center"
			gap="3"
			fontSize="xs"
			color="fg.muted"
		>
			<Box flex="1" borderTopWidth="1px" borderColor="border" />
			{NEW_THREAD_LABEL}
			<Box flex="1" borderTopWidth="1px" borderColor="border" />
		</Flex>
	);
}

/** One question and, once it comes, its answer; an open question is polled. */
function FollowUpTurn({
	question,
	startsThread,
	canRetry,
}: {
	question: FollowUp;
	/** Whether this question starts the oldest thread, a later one, or neither. */
	startsThread: "first" | "later" | null;
	canRetry: boolean;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const { review, actions } = useReview();
	const polled = useQuery({
		...questionQuery(api, question.id),
		enabled: isOpen(question),
	});
	const current = polled.data ?? question;

	useEffect(() => {
		const done = polled.data;
		if (done !== undefined && !isOpen(done)) {
			queryClient.setQueryData(
				keys.threads(review.id),
				(threads: Thread[] = []) => withQuestion(threads, done),
			);
		}
	}, [polled.data, queryClient, review.id]);

	const { ask } = actions;
	const retry = () =>
		ask.mutate({
			question: askedWords(question, ask),
			threadId: question.thread_id,
		});

	const asked = (
		<ChatCard title="Follow-up question" tone="person" at={question.asked_at}>
			<MaskedText text={question.question_sent} />
		</ChatCard>
	);

	return (
		<>
			{startsThread === null ? (
				asked
			) : (
				<Stack
					id={threadAnchor(question.thread_id)}
					tabIndex={-1}
					gap="4"
					rounded="card"
				>
					{startsThread === "later" ? <ThreadDivider /> : null}
					{asked}
				</Stack>
			)}
			{isOpen(current) ? (
				polled.isError ? (
					<ErrorCard
						title="The answer couldn't be checked"
						error={polled.error}
						onRetry={() => void polled.refetch()}
					/>
				) : (
					<PendingReply doing="Searching the evidence" />
				)
			) : current.status === "failed" ? (
				<ChatCard
					title="The answer couldn't be made"
					tone="error"
					at={current.answered_at ?? undefined}
					actions={
						canRetry ? (
							<Button size="sm" onClick={retry}>
								Try again
							</Button>
						) : undefined
					}
				>
					<Text>{current.error ?? "Something went wrong. Ask again."}</Text>
				</ChatCard>
			) : (
				<AnswerCard question={current} words={askedWords(question, ask)} />
			)}
		</>
	);
}

function AnswerCard({
	question,
	words,
}: {
	question: FollowUp;
	/** What "Change the search" starts from when nothing answers. */
	words: string;
}) {
	const openSource = useContext(AnswerSourceContext);
	const { canManage } = useReview();
	const { changeSearch } = useAsk();
	const answer = question.answer;
	const sources = answer?.sources ?? [];
	const chips = useMemo<ChipSource[]>(
		() =>
			sources.map((source) => ({
				tag: source.tag,
				has_passage: source.passages.length > 0,
				source_kind: source.source_kind,
				title: null,
				year: null,
			})),
		[sources],
	);
	const masked = totalMasked(question.masked);
	const isNothing = answer === null || answer.nothing_found;
	const facts = [
		isNothing ? null : plural(sources.length, "source"),
		masked > 0 ? `${plural(masked, "item")} masked` : "nothing to mask",
	].filter((fact) => fact !== null);
	const warnings = answer?.warnings.length ?? 0;
	const onOpen: OnOpenSource = (tag, chip) =>
		openSource({
			tag,
			source: sources.find((s) => s.tag === tag),
			runId: answer?.run_id ?? null,
			chip,
		});

	return (
		<ChatCard
			title="Answer"
			at={question.answered_at ?? undefined}
			actions={
				isNothing && canManage && changeSearch !== null ? (
					<Button
						size="sm"
						variant="outline"
						onClick={() => changeSearch(words)}
					>
						<SlidersHorizontal />
						{CHANGE_LABEL}
					</Button>
				) : undefined
			}
		>
			{isNothing ? (
				<Text>{answer?.message ?? NOTHING_FOUND}</Text>
			) : (
				<AnswerClaims claims={answer.claims} sources={chips} onOpen={onOpen} />
			)}
			{warnings === 0 ? null : (
				<Text display="flex" gap="1.5" alignItems="center">
					<TriangleAlert size={16} />
					{`${plural(warnings, "check")} still failing`}
				</Text>
			)}
			<Text
				display="flex"
				gap="1.5"
				alignItems="center"
				fontSize="xs"
				color="fg.muted"
			>
				<ShieldCheck size={14} aria-hidden="true" />
				{facts.join(" · ")}
			</Text>
		</ChatCard>
	);
}

const wordsOf = (text: string) =>
	text.split(/\s+/).filter((word) => word !== "");

/** The claims cut to their first `limit` words, or null when they fit. */
function previewOf(claims: readonly Claim[], limit: number): Claim[] | null {
	const total = claims.reduce((sum, c) => sum + wordsOf(c.text).length, 0);
	if (total <= limit) {
		return null;
	}
	const shown: Claim[] = [];
	let left = limit;
	for (const claim of claims) {
		const words = wordsOf(claim.text);
		if (words.length >= left) {
			shown.push({ ...claim, text: `${words.slice(0, left).join(" ")}…` });
			break;
		}
		shown.push(claim);
		left -= words.length;
	}
	return shown;
}

/** An answer's claims; past 60 words, the rest waits behind Read all (§5.3.9). */
function AnswerClaims({
	claims,
	sources,
	onOpen,
}: {
	claims: readonly Claim[];
	sources: readonly ChipSource[];
	onOpen: OnOpenSource;
}) {
	const [isWhole, setWhole] = useState(false);
	const id = useId();
	const preview = useMemo(() => previewOf(claims, PREVIEW_WORDS), [claims]);
	if (preview === null) {
		return <Claims claims={claims} sources={sources} onOpen={onOpen} />;
	}
	return (
		<>
			<Box id={id}>
				<Claims
					claims={isWhole ? claims : preview}
					sources={sources}
					onOpen={onOpen}
				/>
			</Box>
			{/* The button stays and toggles, so focus never falls back to the page. */}
			<Button
				type="button"
				size="xs"
				variant="ghost"
				alignSelf="flex-start"
				aria-expanded={isWhole}
				aria-controls={id}
				onClick={() => setWhole(!isWhole)}
			>
				{isWhole ? "Show less" : "Read all"}
			</Button>
		</>
	);
}
