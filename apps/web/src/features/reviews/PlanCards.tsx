/**
 * The planning half of the chat (UI_UX_SPEC §5.3.1 to 5.3.4, §5.3.10): the
 * question, privacy receipts, clarifying questions, change requests, drafts
 * and the error card. Each reads the review from context and sends through
 * its actions; none decides what the API allows.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Check, Info, ShieldCheck, TriangleAlert } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { ApiError, isApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { planVersionQuery } from "../../app/reviewQueries";
import { LinkButton, linkStyle } from "../../ui/LinkButton";
import { PIPELINE } from "../runs/runEvents";
import { ChatCard, ChatLine } from "./ChatCard";
import { modeLabel, type PlanMode, plural } from "./format";
import { MaskedText } from "./MaskedText";
import { ModeChoice } from "./ModeChoice";
import { maskedCounts, totalMasked } from "./masking";
import { useReview, type Version } from "./reviewContext";

type Schemas = components["schemas"];
type Receipt = Schemas["ReceiptOut"];
type SentText = Schemas["SentTextOut"];

const SENDING = "Sending…";

function Sending() {
	return (
		<Text role="status" color="fg.muted" fontSize="xs">
			{SENDING}
		</Text>
	);
}

/** A failed request as a card (§5.3.10): the API's words, a retry, and the request id. */
export function ErrorCard({
	title,
	error,
	onRetry,
}: {
	title: string;
	error: unknown;
	onRetry?: () => void;
}) {
	const [isOpen, setOpen] = useState(false);
	const detailsId = useId();
	if (isApiError(error, "signed-out")) {
		return null;
	}
	const known = error instanceof ApiError ? error : null;
	return (
		<ChatCard
			title={title}
			tone="error"
			actions={
				<>
					{onRetry === undefined ? null : (
						<Button size="sm" onClick={onRetry}>
							Try again
						</Button>
					)}
					{known === null ? null : (
						<LinkButton
							type="button"
							fontSize="sm"
							aria-expanded={isOpen}
							aria-controls={detailsId}
							onClick={() => setOpen(!isOpen)}
						>
							Details
						</LinkButton>
					)}
				</>
			}
		>
			<Text>
				{known === null
					? "Something went wrong in this page. Reload to try again."
					: userMessage(known)}
			</Text>
			{known !== null && isOpen ? (
				<Text id={detailsId} fontSize="xs" color="fg.muted">
					Status {known.status} · Request id {known.requestId ?? "none"}
				</Text>
			) : null}
		</ChatCard>
	);
}

export function QuestionTurn({ isSending }: { isSending: boolean }) {
	const { review, plan } = useReview();
	return (
		<ChatCard title="Question" tone="person" at={review.created_at}>
			<Text whiteSpace="pre-wrap" overflowWrap="anywhere" fontSize="md">
				{review.question}
			</Text>
			{plan.mode === null ? null : (
				<Text color="fg.muted" fontSize="xs">
					{modeLabel(plan.mode)}
				</Text>
			)}
			{isSending ? <Sending /> : null}
		</ChatCard>
	);
}

/**
 * What one model call received (§5.3.2, §8.5). Counts by type, never the
 * original values; the masked text is exactly what the API says was sent.
 */
export function PrivacyReceipt({
	receipt,
	item,
	isOpenAtFirst,
}: {
	receipt: Receipt;
	item: SentText;
	isOpenAtFirst: boolean;
}) {
	const [isOpen, setOpen] = useState(isOpenAtFirst);
	const id = useId();
	const total = totalMasked(item.counts);
	return (
		<Box>
			<ChatLine
				icon={<ShieldCheck size={16} />}
				actions={
					<LinkButton
						type="button"
						fontSize="sm"
						aria-expanded={isOpen}
						aria-controls={`${id}-sent`}
						onClick={() => setOpen(!isOpen)}
					>
						Show what was sent
					</LinkButton>
				}
			>
				<span>
					{total > 0
						? `Sent with ${plural(total, "item")} masked`
						: "Sent as written · nothing to mask"}
				</span>
			</ChatLine>
			{isOpen ? (
				<Box
					as="section"
					id={`${id}-sent`}
					aria-labelledby={`${id}-title`}
					mt="2"
					px="4"
					py="3"
					rounded="card"
					borderWidth="1px"
					borderColor="border"
					bg="bg.panel"
					fontSize="sm"
					css={{ "& > * + *": { mt: "2" } }}
				>
					<Text id={`${id}-title`} fontWeight="medium">
						What the model received
					</Text>
					<MaskedText text={item.sent} />
					{total > 0 ? (
						<>
							<Text>Masked: {maskedCounts(item.counts)}.</Text>
							<Text color="caveat.fg">
								Masking is not anonymity: check codes and numbers yourself.
							</Text>
						</>
					) : null}
					{receipt.delivered ? null : (
						<Text color="caveat.fg">
							The call to the model failed after the text was sent, so it may
							still have been read.
						</Text>
					)}
					<Text fontSize="xs" color="fg.muted">
						Sent to {receipt.model_id}
					</Text>
				</Box>
			) : null}
		</Box>
	);
}

export function StartPlanCard() {
	const { canManage, actions } = useReview();
	const [mode, setMode] = useState<PlanMode>("deep");
	return (
		<ChatCard
			title="The search plan hasn't been made yet"
			actions={
				canManage ? (
					<Button
						size="sm"
						disabled={actions.startPlan.isPending}
						onClick={() => actions.startPlan.mutate(mode)}
					>
						Make the search plan
					</Button>
				) : undefined
			}
		>
			{canManage ? (
				<ModeChoice value={mode} onChange={setMode} />
			) : (
				<Text>Only owners and reviewers can make it.</Text>
			)}
		</ChatCard>
	);
}

const Chip = chakra("button", {
	base: {
		minH: "8",
		px: "3",
		rounded: "pill",
		borderWidth: "1px",
		borderColor: "border.emphasized",
		bg: "bg.panel",
		color: "fg",
		fontSize: "sm",
		cursor: "pointer",
		transition: "background-color 0.2s ease",
		_hover: { bg: "bg.muted" },
	},
});

/** Deep mode's questions, one at a time, with suggestions as chips (§5.3.3). */
export function ClarifyCard({ draft }: { draft: string }) {
	const { plan, canManage, actions, setComposerText, focusComposer } =
		useReview();
	const questions = plan.questions;
	const index = questions.findIndex((q) => q.answer === null && !q.skipped);
	const question = questions[index];
	const pending = actions.answer.isPending
		? actions.answer.variables
		: undefined;
	const total = questions.length;

	if (question === undefined) {
		return null;
	}
	const pick = (text: string) => {
		setComposerText(text);
		focusComposer();
	};
	const send = () => {
		const text = draft.trim();
		if (text !== "") {
			setComposerText("");
			actions.answer.mutate({ questionId: question.id, answer: text });
		}
	};

	return (
		<ChatCard
			title={`Search planner · ${plural(total, "question")} before the search`}
			actions={
				canManage ? (
					<>
						<Button
							size="sm"
							disabled={draft.trim() === "" || actions.answer.isPending}
							onClick={send}
						>
							Answer
						</Button>
						<LinkButton
							type="button"
							fontSize="sm"
							disabled={actions.answer.isPending}
							onClick={() =>
								actions.answer.mutate({ questionId: question.id, answer: null })
							}
						>
							Skip this one
						</LinkButton>
						<LinkButton
							type="button"
							fontSize="sm"
							disabled={actions.answer.isPending}
							onClick={() => actions.answer.mutate({ draftNow: true })}
						>
							Skip all and draft now
						</LinkButton>
					</>
				) : undefined
			}
		>
			<Text fontSize="md">
				<Text as="span" color="fg.muted" fontSize="sm" mr="2">
					{index + 1} of {total}
				</Text>
				{question.text}
			</Text>
			{canManage && question.suggestions.length > 0 ? (
				<Box display="flex" flexWrap="wrap" gap="2">
					{question.suggestions.map((suggestion) => (
						<Chip
							key={suggestion}
							type="button"
							onClick={() => pick(suggestion)}
						>
							{suggestion}
						</Chip>
					))}
					<Chip type="button" onClick={() => pick("")}>
						Other…
					</Chip>
				</Box>
			) : null}
			{pending !== undefined && "answer" in pending ? (
				<Text color="fg.muted">
					{pending.answer === null
						? "Skipping…"
						: `Your answer: ${pending.answer}`}
				</Text>
			) : null}
			{actions.answer.isPending ? <Sending /> : null}
		</ChatCard>
	);
}

/** Once the questions are settled, they fold to one line (§5.3.3 L0). */
export function AnsweredLine({ receipts }: { receipts: readonly Receipt[] }) {
	const { plan } = useReview();
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const total = plan.questions.length;
	const skipped = plan.questions.filter((q) => q.skipped).length;
	const sent = receipts
		.flatMap((r) => r.items)
		.filter((i) => i.kind === "answer");
	return (
		<Box>
			<ChatLine
				icon={<Check size={16} />}
				actions={
					<LinkButton
						type="button"
						fontSize="sm"
						aria-expanded={isOpen}
						aria-controls={id}
						onClick={() => setOpen(!isOpen)}
					>
						Show answers
					</LinkButton>
				}
			>
				<span>
					{skipped === 0
						? `${plural(total, "clarifying question")} answered`
						: `${plural(total, "clarifying question")} · ${total - skipped} answered, ${skipped} skipped`}
				</span>
			</ChatLine>
			{isOpen ? (
				<chakra.ul id={id} mt="2" pl="6" fontSize="sm">
					{plan.questions.map((q) => {
						const item = sent.find((s) => s.ref === q.id);
						const masked = item === undefined ? 0 : totalMasked(item.counts);
						return (
							<li key={q.id}>
								{q.text} — {q.answer ?? "Skipped"}
								{masked > 0 ? (
									<Text as="span" color="fg.muted">
										{" "}
										· sent with {plural(masked, "item")} masked
									</Text>
								) : null}
							</li>
						);
					})}
				</chakra.ul>
			) : null}
		</Box>
	);
}

/** A change request as the person's turn; `text` is what they wrote. */
export function ChangeTurn({
	text,
	at,
	isSending = false,
}: {
	text: string;
	at?: string;
	isSending?: boolean;
}) {
	return (
		<ChatCard title="Change request" tone="person" at={at}>
			<Text whiteSpace="pre-wrap" overflowWrap="anywhere">
				{text}
			</Text>
			{isSending ? <Sending /> : null}
		</ChatCard>
	);
}

/** An older version's change request, which only that version's own record carries. */
export function PastChangeTurn({ version }: { version: number }) {
	const api = useApi();
	const { review } = useReview();
	const detail = useQuery(planVersionQuery(api, review.id, version));
	const request = detail.data?.plan.change_request;
	return request == null ? null : (
		<ChangeTurn text={request.text} at={request.requested_at} />
	);
}

function conceptCount(version: Version): number {
	return version.themes.reduce((sum, theme) => sum + theme.concepts.length, 0);
}

export const categoryCount = (version: Version) =>
	plural(version.themes.length, "category", "categories");

function DraftLink({
	version,
	children,
}: {
	version?: number;
	children: ReactNode;
}) {
	const { project, review } = useReview();
	return (
		<ChakraLink asChild css={linkStyle} fontSize="sm">
			<Link
				to="/p/$projectId/r/$reviewId/draft"
				params={{ projectId: project.id, reviewId: review.id }}
				search={version === undefined ? {} : { version }}
			>
				{children}
			</Link>
		</ChakraLink>
	);
}

/** "Accept and search", from the card or the canvas: one request, one pending state. */
export function AcceptButton({ version }: { version: number }) {
	const { actions } = useReview();
	const isBusy = actions.accept.isPending || actions.startRun.isPending;
	return (
		<Button
			size="sm"
			disabled={isBusy}
			onClick={() => actions.accept.mutate(version)}
		>
			{isBusy ? "Starting the search…" : "Accept and search"}
		</Button>
	);
}

/** What went wrong accepting or starting, said once wherever the button is. */
export function AcceptProblem() {
	const { actions } = useReview();
	const { accept, startRun } = actions;
	if (isApiError(accept.error, "conflict")) {
		return (
			<Text
				role="alert"
				color="caveat.fg"
				display="flex"
				gap="1.5"
				alignItems="center"
			>
				<TriangleAlert size={16} />
				{accept.error.problem.detail ??
					"The draft has changed. Showing the latest."}
			</Text>
		);
	}
	if (accept.isError) {
		return (
			<ErrorCard title="The draft couldn't be accepted" error={accept.error} />
		);
	}
	if (startRun.isError) {
		return (
			<ErrorCard
				title="The search couldn't be started"
				error={startRun.error}
				onRetry={() => {
					if (startRun.variables !== undefined) {
						startRun.mutate(startRun.variables);
					}
				}}
			/>
		);
	}
	return null;
}

/** The latest draft, waiting to be accepted (§5.3.4 L1). */
export function DraftCard({ version }: { version: Version }) {
	const { canManage, focusComposer } = useReview();
	const notes = version.notes.length;
	return (
		<ChatCard
			title={`Search planner · Draft ${version.version}`}
			at={version.created_at}
			actions={
				<>
					{canManage ? <AcceptButton version={version.version} /> : null}
					<DraftLink>Open draft</DraftLink>
					{canManage && !version.is_strict ? (
						<LinkButton type="button" fontSize="sm" onClick={focusComposer}>
							Ask for changes
						</LinkButton>
					) : null}
				</>
			}
		>
			<Text color="fg.muted">
				{categoryCount(version)} · {plural(conceptCount(version), "concept")}
			</Text>
			{version.is_strict ? (
				<Text>Your terms, used exactly as given</Text>
			) : null}
			<chakra.ol pl="5" css={{ "& > li + li": { mt: "1" } }}>
				{version.themes.map((theme) => (
					<li key={theme.name}>
						{`${theme.name}: ${theme.concepts.map((c) => c.term).join(" or ")}`}
					</li>
				))}
			</chakra.ol>
			<Text fontSize="xs" color="fg.muted">
				A paper must match every category.
			</Text>
			{notes > 0 ? (
				<Text display="flex" gap="1.5" alignItems="center" color="fg.muted">
					<Info size={16} />
					{plural(notes, "change")} made automatically · see the draft
				</Text>
			) : null}
			<AcceptProblem />
		</ChatCard>
	);
}

/** A draft that a newer one replaced, folded to one line (§5.2, §5.3.4 L0). */
export function ReplacedDraftLine({
	version,
	by,
}: {
	version: number;
	by: number;
}) {
	return (
		<ChatLine actions={<DraftLink version={version}>Open</DraftLink>}>
			<span>{`Draft ${version} · replaced by draft ${by}`}</span>
		</ChatLine>
	);
}

/** The accepted version, folded (§5.3.4 L0), with a way to start if no run has. */
export function AcceptedLine({ version }: { version: Version }) {
	const { canManage, runs, actions } = useReview();
	const canStart =
		canManage && runs.length === 0 && !actions.startRun.isPending;
	return (
		<Box>
			<ChatLine
				icon={<Check size={16} />}
				actions={
					<>
						<DraftLink>Open</DraftLink>
						{canStart ? (
							<Button
								size="xs"
								onClick={() => actions.startRun.mutate(PIPELINE)}
							>
								Start the search
							</Button>
						) : null}
					</>
				}
			>
				<span>{`Search plan accepted · version ${version.version} · ${categoryCount(version)}`}</span>
			</ChatLine>
			<Box mt="2">
				<AcceptProblem />
			</Box>
		</Box>
	);
}
