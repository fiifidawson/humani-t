/**
 * The owner's masking review of one document (UI_UX_SPEC §6.6): the original
 * text with what is masked marked, the masked text beside it, every mask with
 * "Not personal" where the API allows it, and masking what was missed.
 *
 * Offsets come from the API's `raw_text` and go back to it as they are; the API
 * decides what may be masked or unmasked, and this only shows its answer.
 */

import {
	Box,
	Button,
	chakra,
	Flex,
	Grid,
	Heading,
	HStack,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, EyeOff, Shield } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useRef, useState } from "react";
import { unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { addMaskingRule, type DocumentReview } from "../../app/documentQueries";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { EntityTypeSelect } from "../reviews/EntityTypeSelect";
import { MaskedText } from "../reviews/MaskedText";
import { type EntityType, entityTypeName } from "../reviews/masking";

type Span = DocumentReview["spans"][number];

interface Range {
	start: number;
	end: number;
}

/** The spans over one range of the original, shown as one mask. */
interface Mask extends Range {
	type: EntityType;
	sources: string[];
	canRemove: boolean;
}

type Correction =
	| { kind: "mask"; range: Range; type: EntityType; isRule: boolean }
	| { kind: "unmask"; range: Range; reason: string };

const Mark = chakra("mark", {
	base: { bg: "masked.bg", color: "masked.fg", rounded: "sm", px: "0.5" },
});

function masksOf(spans: readonly Span[]): Mask[] {
	const masks = new Map<string, Mask>();
	for (const span of spans) {
		const key = `${span.start}-${span.end}`;
		const mask = masks.get(key);
		if (mask === undefined) {
			masks.set(key, {
				start: span.start,
				end: span.end,
				type: span.entity_type,
				sources: [span.source],
				canRemove: span.can_remove,
			});
		} else {
			mask.sources.push(span.source);
			mask.canRemove &&= span.can_remove;
		}
	}
	return [...masks.values()].sort((a, b) => a.start - b.start);
}

function madeBy(sources: readonly string[]): string {
	if (sources.some((s) => s.startsWith("rule:"))) {
		return "Project rule";
	}
	return sources.some((s) => s.startsWith("user:"))
		? "Masked by an owner"
		: "Found automatically";
}

/** The original cut into plain and masked runs; overlapping masks join, as in the API. */
function runs(raw: string, spans: readonly Span[]) {
	const masked: Range[] = [];
	for (const span of [...spans].sort((a, b) => a.start - b.start)) {
		const last = masked.at(-1);
		if (last !== undefined && span.start < last.end) {
			last.end = Math.max(last.end, span.end);
		} else {
			masked.push({ start: span.start, end: span.end });
		}
	}
	const all: (Range & { isMasked: boolean })[] = [];
	let cursor = 0;
	for (const range of masked) {
		if (range.start > cursor) {
			all.push({ start: cursor, end: range.start, isMasked: false });
		}
		all.push({ ...range, isMasked: true });
		cursor = range.end;
	}
	if (cursor < raw.length) {
		all.push({ start: cursor, end: raw.length, isMasked: false });
	}
	return all;
}

/** Where a selection point falls in the original, if it is inside one of its runs. */
function offsetIn(
	pane: HTMLElement,
	node: Node,
	offset: number,
): number | null {
	const holder = node.nodeType === Node.TEXT_NODE ? node.parentElement : null;
	const start = holder?.dataset["offset"];
	if (holder === null || start === undefined || !pane.contains(holder)) {
		return null;
	}
	return Number(start) + offset;
}

function Pane({ title, children }: { title: string; children: ReactNode }) {
	const headingId = useId();
	return (
		<Stack as="section" aria-labelledby={headingId} gap="2" minW="0">
			<Heading id={headingId} as="h4" fontSize="md" fontWeight="semibold">
				{title}
			</Heading>
			<Box p="4" rounded="card" borderWidth="1px" borderColor="border" bg="bg">
				{children}
			</Box>
		</Stack>
	);
}

export function MaskingCorrections({
	projectId,
	review,
}: {
	projectId: string;
	review: DocumentReview;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const original = useRef<HTMLParagraphElement>(null);
	const typeId = useId();
	const [selected, setSelected] = useState<Range | null>(null);
	const [type, setType] = useState<EntityType>("PERSON");
	const [removing, setRemoving] = useState<Range | null>(null);
	const [reason, setReason] = useState("");
	const [outcome, setOutcome] = useState<string | null>(null);
	const raw = review.raw_text;
	const quoted = ({ start, end }: Range) => raw.slice(start, end);
	const reviewKey = keys.documentReview(projectId, review.document_id);

	const correct = useMutation({
		mutationFn: async (correction: Correction) => {
			const params = {
				path: { project_id: projectId, document_id: review.document_id },
			};
			const { start, end } = correction.range;
			if (correction.kind === "unmask") {
				return unwrap(
					api.POST(
						"/api/v1/projects/{project_id}/documents/{document_id}/unmask",
						{
							params,
							body: {
								version: review.mask_version,
								start,
								end,
								reason: correction.reason,
							},
						},
					),
				);
			}
			if (correction.isRule) {
				await addMaskingRule(api, projectId, {
					term: quoted(correction.range),
					entity_type: correction.type,
				});
			}
			// A new rule reaches a document only at its next correction; masking this
			// range is that correction, so every other occurrence is masked now too.
			return unwrap(
				api.POST(
					"/api/v1/projects/{project_id}/documents/{document_id}/masks",
					{
						params,
						body: {
							version: review.mask_version,
							start,
							end,
							entity_type: correction.type,
						},
					},
				),
			);
		},
		onMutate: () => setOutcome(null),
		onSuccess: async (updated, correction) => {
			const words = `“${quoted(correction.range)}”`;
			setOutcome(
				correction.kind === "unmask"
					? `${words} is no longer masked.`
					: correction.isRule
						? `${words} is now masked everywhere in this project.`
						: `${words} is masked.`,
			);
			setSelected(null);
			setRemoving(null);
			setReason("");
			queryClient.setQueryData(reviewKey, updated);
			await queryClient.invalidateQueries({
				queryKey: keys.documents(projectId),
				exact: true,
			});
		},
	});

	const readSelection = () => {
		const pane = original.current;
		const selection = window.getSelection();
		if (pane === null || selection === null || selection.rangeCount === 0) {
			return;
		}
		const range = selection.getRangeAt(0);
		const start = offsetIn(pane, range.startContainer, range.startOffset);
		const end = offsetIn(pane, range.endContainer, range.endOffset);
		setSelected(
			start === null || end === null || start >= end ? null : { start, end },
		);
	};

	const unmask = (event: FormEvent) => {
		event.preventDefault();
		if (removing !== null && reason.trim() !== "" && !correct.isPending) {
			correct.mutate({ kind: "unmask", range: removing, reason });
		}
	};

	const masks = masksOf(review.spans);

	return (
		<Stack gap="5">
			<Grid templateColumns={{ base: "1fr", lg: "1fr 1fr" }} gap="4">
				<Pane title="Original text">
					<Text
						ref={original}
						whiteSpace="pre-wrap"
						overflowWrap="anywhere"
						onMouseUp={readSelection}
						onKeyUp={readSelection}
					>
						{runs(raw, review.spans).map((run) =>
							run.isMasked ? (
								<Mark key={run.start}>
									<span data-offset={run.start}>{quoted(run)}</span>
								</Mark>
							) : (
								<span key={run.start} data-offset={run.start}>
									{quoted(run)}
								</span>
							),
						)}
					</Text>
				</Pane>
				<Pane title="What the model will see">
					<MaskedText text={review.masked_text} />
				</Pane>
			</Grid>

			<Stack
				gap="3"
				p="4"
				rounded="card"
				borderWidth="1px"
				borderColor="border"
			>
				{selected === null ? (
					<Text fontSize="sm" color="fg.muted">
						Select words in the original text to mask something that was missed.
					</Text>
				) : (
					<>
						<Text fontWeight="medium" overflowWrap="anywhere">
							{`Selected: “${quoted(selected)}”`}
						</Text>
						<Flex gap="3" align="center" wrap="wrap">
							<chakra.label htmlFor={typeId} fontSize="sm" fontWeight="medium">
								Mask as
							</chakra.label>
							<EntityTypeSelect id={typeId} value={type} onChange={setType} />
							<Button
								size="sm"
								disabled={correct.isPending}
								onClick={() =>
									correct.mutate({
										kind: "mask",
										range: selected,
										type,
										isRule: false,
									})
								}
							>
								<Shield />
								Mask it
							</Button>
							<Button
								size="sm"
								variant="outline"
								disabled={correct.isPending}
								onClick={() =>
									correct.mutate({
										kind: "mask",
										range: selected,
										type,
										isRule: true,
									})
								}
							>
								Always mask in this project
							</Button>
						</Flex>
					</>
				)}
			</Stack>

			<Box aria-live="polite">
				{outcome === null ? null : <Text fontWeight="medium">{outcome}</Text>}
			</Box>
			{correct.isError ? (
				<ErrorNotice
					error={correct.error}
					// Someone else changed the masking: reading it again shows theirs.
					{...(isApiError(correct.error, "conflict")
						? {
								onRetry: () =>
									void queryClient.invalidateQueries({ queryKey: reviewKey }),
							}
						: {})}
				/>
			) : null}

			<Stack gap="2">
				<Heading as="h4" fontSize="md" fontWeight="semibold">
					{`Masks (${masks.length})`}
				</Heading>
				{masks.length === 0 ? (
					<Text>Nothing is masked.</Text>
				) : (
					<Stack as="ul" aria-label="Masks" gap="2" listStyle="none">
						{masks.map((mask) => {
							const words = quoted(mask);
							const isRule = mask.sources.some((s) => s.startsWith("rule:"));
							const isRemoving =
								removing?.start === mask.start && removing.end === mask.end;
							return (
								<Stack
									as="li"
									key={`${mask.start}-${mask.end}`}
									gap="2"
									py="2"
									borderBottomWidth="1px"
									borderColor="border"
								>
									<Flex
										gap="3"
										align="center"
										justify="space-between"
										wrap="wrap"
									>
										<Text overflowWrap="anywhere">
											{`“${words}” · ${entityTypeName(mask.type)} · ${madeBy(mask.sources)}`}
										</Text>
										<HStack gap="2" wrap="wrap">
											{mask.canRemove && !isRemoving ? (
												<Button
													size="xs"
													variant="outline"
													aria-label={`Not personal: ${words}`}
													onClick={() => {
														setRemoving(mask);
														setReason("");
													}}
												>
													<EyeOff />
													Not personal
												</Button>
											) : null}
											{isRule ? null : (
												<Button
													size="xs"
													variant="outline"
													aria-label={`Always mask in this project: ${words}`}
													disabled={correct.isPending}
													onClick={() =>
														correct.mutate({
															kind: "mask",
															range: mask,
															type: mask.type,
															isRule: true,
														})
													}
												>
													Always mask in this project
												</Button>
											)}
										</HStack>
									</Flex>
									{isRemoving ? (
										<Stack as="form" gap="2" onSubmit={unmask}>
											<Field
												label="Why it isn't personal"
												hint="One line. It is kept with the review."
												maxLength={200}
												required
												value={reason}
												onChange={(event) => setReason(event.target.value)}
											/>
											<HStack gap="2">
												<Button
													type="submit"
													size="sm"
													disabled={reason.trim() === "" || correct.isPending}
												>
													<Check />
													Remove mask
												</Button>
												<Button
													size="sm"
													variant="ghost"
													onClick={() => setRemoving(null)}
												>
													Cancel
												</Button>
											</HStack>
										</Stack>
									) : null}
								</Stack>
							);
						})}
					</Stack>
				)}
			</Stack>
		</Stack>
	);
}
