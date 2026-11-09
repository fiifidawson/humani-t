/**
 * Follow-up questions in the chat (UI_UX_SPEC §5.3.9, §6.12): the choice
 * above the box once a brief exists, each question with its answer in the
 * conversation, and the source panel for an answer's citations.
 *
 * Asking returns at once and the worker answers in the background, so an
 * open question is polled until it is answered or failed (docs/api.md
 * "Follow-up questions"). Every question continues the latest thread. What
 * may be asked, and by whom, is the API's rule; this hides only what the
 * caller's role cannot do.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
	MessageCircleQuestion,
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
	useMemo,
	useState,
} from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { briefQuery } from "../../app/briefQueries";
import { keys } from "../../app/queries";
import { questionQuery, threadsQuery } from "../../app/reviewQueries";
import { type ChipSource, Claims } from "../briefs/Claim";
import { Passages, SourcePanelFrame } from "../briefs/SourcePanel";
import { PendingReply } from "../chat/PendingReply";
import { ChatCard } from "./ChatCard";
import { plural } from "./format";
import { MaskedText } from "./MaskedText";
import { totalMasked } from "./masking";
import { ErrorCard } from "./PlanCards";
import {
	type FollowUp,
	type Thread,
	useReview,
	withQuestion,
} from "./reviewContext";

type AnswerSource = components["schemas"]["AnswerSourceOut"];

export const ASK_LABEL = "Ask about the evidence";
const CHANGE_LABEL = "Change the search";
const NOTHING_FOUND = "Nothing in this project answers that.";

export type ComposerChoice = "ask" | "change";

const isOpen = (question: FollowUp) =>
	question.status === "pending" || question.status === "answering";

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

/** What the box does while asking (§5.4 "A brief exists"): it continues the latest thread. */
export function useAskSetup() {
	const api = useApi();
	const { review, actions } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const { ask } = actions;
	const latest = threads.data?.[0];
	const last = latest?.questions.at(-1);
	const reason = threads.isError
		? "The questions so far couldn't be loaded. Reload the page to try again."
		: threads.data === undefined
			? "Loading the questions so far…"
			: ask.isPending || (last !== undefined && isOpen(last))
				? "Wait for the answer to your last question."
				: null;
	return {
		label: ASK_LABEL,
		placeholder: "Ask a follow-up",
		note: "Answers use only the papers and documents in this project.",
		...(reason === null ? {} : { unavailableReason: reason }),
		onSend: (text: string) =>
			ask.mutate({ question: text, threadId: latest?.id ?? null }),
	};
}

/** Two pressed-or-not buttons, not a menu: both choices stay in sight. */
export function ComposerChoiceSwitch({
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
		<Flex
			role="group"
			aria-label="What the box does"
			gap="2"
			wrap="wrap"
			pb="2.5"
		>
			{option("ask", <MessageCircleQuestion />, ASK_LABEL)}
			{option("change", <SlidersHorizontal />, CHANGE_LABEL)}
		</Flex>
	);
}

interface OpenSource {
	tag: string;
	source: AnswerSource | undefined;
}

const AnswerSourceContext = createContext<(open: OpenSource) => void>(
	() => undefined,
);

/**
 * Holds the source panel for answers' citations. An answer carries its own
 * passages, which may be ones the brief never cited, so the panel shows those
 * rather than the brief's evidence.
 */
export function AnswerSourceProvider({ children }: { children: ReactNode }) {
	const [open, setOpen] = useState<OpenSource | null>(null);
	const close = useCallback(() => setOpen(null), []);
	return (
		<AnswerSourceContext value={setOpen}>
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
							<Text>
								{open.source.source_kind === "document"
									? "One of your project's documents."
									: "A paper in this review."}
							</Text>
							<Passages passages={open.source.passages} />
						</Stack>
					)}
				</SourcePanelFrame>
			)}
		</AnswerSourceContext>
	);
}

/**
 * Each question with its answer, stamped with when it was asked, for the
 * conversation to place among the drafts and runs: after the brief it follows.
 */
export function useFollowUpTurns(): { at: string; node: ReactNode }[] {
	const api = useApi();
	const { review, canManage, actions } = useReview();
	const threads = useQuery(threadsQuery(api, review.id));
	const all = threads.data ?? [];
	const lastAsked = all[0]?.questions.at(-1);
	const canRetry = canManage && !actions.ask.isPending;
	return all.flatMap((thread) =>
		thread.questions.map((question) => ({
			at: question.asked_at,
			node: (
				<FollowUpTurn
					key={`question-${question.id}`}
					question={question}
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

/** One question and, once it comes, its answer; an open question is polled. */
function FollowUpTurn({
	question,
	canRetry,
}: {
	question: FollowUp;
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
			// The masked text is what the model saw before; the words typed in
			// this visit are better still, since masking runs on them again.
			question:
				ask.data?.id === question.id && ask.variables !== undefined
					? ask.variables.question
					: question.question_sent,
			threadId: question.thread_id,
		});

	return (
		<>
			<ChatCard title="Follow-up question" tone="person" at={question.asked_at}>
				<MaskedText text={question.question_sent} />
			</ChatCard>
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
				<AnswerCard question={current} />
			)}
		</>
	);
}

function AnswerCard({ question }: { question: FollowUp }) {
	const openSource = useContext(AnswerSourceContext);
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

	return (
		<ChatCard title="Answer" at={question.answered_at ?? undefined}>
			{isNothing ? (
				<Text>{answer?.message ?? NOTHING_FOUND}</Text>
			) : (
				<Claims
					claims={answer.claims}
					sources={chips}
					onOpen={(tag) =>
						openSource({ tag, source: sources.find((s) => s.tag === tag) })
					}
				/>
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
