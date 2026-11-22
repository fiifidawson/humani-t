/**
 * The owner's masking review of one document (UI_UX_SPEC §6.6): the original
 * and the masked text side by side in one scrolling frame. Selecting words in
 * the original masks them; clicking a mask opens its card ("Not personal" where
 * the API allows it). The full list of masks stays below, for the keyboard.
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
	VisuallyHidden,
} from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, EyeOff, Shield } from "lucide-react";
import {
	type FormEvent,
	type ReactNode,
	useCallback,
	useEffect,
	useId,
	useRef,
	useState,
} from "react";
import { unwrap } from "../../api/client";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import {
	addMaskingRule,
	type DocumentReview,
	type DocumentType,
} from "../../app/documentQueries";
import { keys } from "../../app/queries";
import { COMPACT_ZOOM } from "../../shell/railStyles";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Reveal } from "../../ui/Reveal";
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
	base: {
		bg: "masked.bg",
		color: "masked.fg",
		rounded: "sm",
		px: "0.5",
		cursor: "pointer",
	},
});

const maskKey = ({ start, end }: Range) => `${start}-${end}`;

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

// A PDF line that ends a sentence ends its paragraph; any other line break in a
// PDF is only where the printed page wrapped.
const ENDS_SENTENCE = /[.!?:;]["”’')\]]?$/;

/**
 * `text` in paragraphs, as the document showed them: a Word, text or Markdown
 * file has one per line; a PDF's lines flow together until a blank line or the
 * end of a sentence. Blank lines are left out.
 */
function paragraphs(text: string, fileType: DocumentType): Range[] {
	const found: Range[] = [];
	let start: number | null = null;
	let end = 0;
	let offset = 0;
	for (const line of text.split("\n")) {
		if (line.trim() === "") {
			if (start !== null) {
				found.push({ start, end });
			}
			start = null;
		} else {
			start ??= offset;
			end = offset + line.length;
			if (fileType !== "pdf" || ENDS_SENTENCE.test(line.trimEnd())) {
				found.push({ start, end });
				start = null;
			}
		}
		offset += line.length + 1;
	}
	if (start !== null) {
		found.push({ start, end });
	}
	return found.length > 0 ? found : [{ start: 0, end: text.length }];
}

/**
 * The original's and the masked text's paragraphs, side by side. Masking keeps
 * line breaks, so they pair up; if they ever do not, each side is one block.
 */
function pairedParagraphs(
	raw: string,
	masked: string,
	fileType: DocumentType,
): [Range[], Range[]] {
	const left = paragraphs(raw, fileType);
	const right = paragraphs(masked, fileType);
	return left.length === right.length
		? [left, right]
		: [[{ start: 0, end: raw.length }], [{ start: 0, end: masked.length }]];
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

/** Where something sits inside the frame's scrolled content, in CSS pixels. */
interface Place {
	top: number;
	bottom: number;
	left: number;
}

type Rect = Pick<DOMRect, "top" | "bottom" | "left">;

const NOWHERE: Rect = { top: 0, bottom: 0, left: 0 };

function placeIn(frame: HTMLElement, rect: Rect): Place {
	const box = frame.getBoundingClientRect();
	// The app's zoom makes client rectangles smaller than CSS pixels; this undoes it.
	const scale = frame.offsetWidth > 0 ? box.width / frame.offsetWidth : 1;
	return {
		top: (rect.top - box.top) / scale + frame.scrollTop,
		bottom: (rect.bottom - box.top) / scale + frame.scrollTop,
		left: (rect.left - box.left) / scale,
	};
}

// Keeps a pop-up of `width` inside the frame, however close to its right edge it opens.
const leftWithin = (place: Place, width: string) =>
	`max(0px, min(${place.left}px, calc(100% - ${width})))`;

const POP_UP = {
	position: "absolute",
	zIndex: "2",
	p: "3",
	bg: "bg.panel",
	rounded: "card",
	borderWidth: "1px",
	borderColor: "border",
	boxShadow: "md",
	maxW: "calc(100% - 1rem)",
} as const;

// A native dialog left open: not modal, so the text beside it stays readable.
const CardDialog = chakra("dialog");

/** A masked word's card, opened where it was clicked: what it is, and its actions. */
function MaskCard({
	mask,
	words,
	place,
	isBusy,
	onUnmask,
	onAlwaysMask,
	onClose,
}: {
	mask: Mask;
	words: string;
	place: Place;
	isBusy: boolean;
	onUnmask: (reason: string) => void;
	onAlwaysMask: () => void;
	onClose: () => void;
}) {
	const card = useRef<HTMLDialogElement>(null);
	const [isRemoving, setRemoving] = useState(false);
	const [reason, setReason] = useState("");
	const isRule = mask.sources.some((s) => s.startsWith("rule:"));

	useEffect(() => {
		card.current?.querySelector("button")?.focus();
	}, []);
	useEffect(() => {
		const away = (event: MouseEvent) => {
			if (
				event.target instanceof Node &&
				!card.current?.contains(event.target)
			) {
				onClose();
			}
		};
		document.addEventListener("mousedown", away);
		return () => document.removeEventListener("mousedown", away);
	}, [onClose]);

	return (
		<CardDialog
			ref={card}
			open
			aria-label={`Mask: ${words}`}
			{...POP_UP}
			m="0"
			color="fg"
			top={`${place.bottom + 4}px`}
			left={leftWithin(place, "20rem")}
			w="20rem"
			onKeyDown={(event) => {
				if (event.key === "Escape") {
					event.stopPropagation();
					onClose();
				}
			}}
		>
			<Stack gap="2">
				<Text fontSize="sm" color="fg.muted">
					{`${entityTypeName(mask.type)} · ${madeBy(mask.sources)}`}
				</Text>
				{isRemoving ? (
					<Stack
						as="form"
						gap="2"
						onSubmit={(event: FormEvent) => {
							event.preventDefault();
							if (reason.trim() !== "" && !isBusy) {
								onUnmask(reason);
							}
						}}
					>
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
								size="xs"
								disabled={reason.trim() === "" || isBusy}
							>
								<Check />
								Remove mask
							</Button>
							<Button size="xs" variant="ghost" onClick={onClose}>
								Cancel
							</Button>
						</HStack>
					</Stack>
				) : (
					<HStack gap="2" wrap="wrap">
						{mask.canRemove ? (
							<Button
								size="xs"
								variant="outline"
								aria-label={`Not personal: ${words}`}
								onClick={() => setRemoving(true)}
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
								disabled={isBusy}
								onClick={onAlwaysMask}
							>
								Always mask in this project
							</Button>
						)}
						<Button size="xs" variant="ghost" onClick={onClose}>
							Close
						</Button>
					</HStack>
				)}
			</Stack>
		</CardDialog>
	);
}

/** What the model reads from a document, for anyone who does not see the original. */
export function MaskedDocument({
	text,
	fileType,
}: {
	text: string;
	fileType: DocumentType;
}) {
	return (
		<Box
			as="section"
			aria-label="The masked text"
			tabIndex={0}
			maxH={`calc(70vh / ${COMPACT_ZOOM})`}
			overflowY="auto"
			// Holds the placeholders' screen-reader text, as the review's frame does.
			position="relative"
			p="4"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg"
			css={{
				"& [data-paragraph]:not(:last-child)": { pb: "4" },
				"& [data-paragraph] > p": { whiteSpace: "normal" },
			}}
		>
			{paragraphs(text, fileType).map((paragraph) => (
				<Box key={paragraph.start} data-paragraph="">
					<MaskedText text={text.slice(paragraph.start, paragraph.end)} />
				</Box>
			))}
		</Box>
	);
}

// On wide screens both panes share the outer grid's rows (subgrid), so a
// paragraph and its masked version always start level, however each one wraps.
function Pane({
	title,
	rows,
	children,
}: {
	title: string;
	rows: number;
	children: ReactNode;
}) {
	const headingId = useId();
	return (
		<Box
			as="section"
			aria-labelledby={headingId}
			minW="0"
			display={{ base: "flex", lg: "grid" }}
			flexDirection="column"
			gridRow={{ lg: `1 / span ${rows + 1}` }}
			gridTemplateRows={{ lg: "subgrid" }}
		>
			<Heading
				id={headingId}
				as="h4"
				fontSize="md"
				fontWeight="semibold"
				pb="2"
				position="sticky"
				top="0"
				zIndex="1"
				bg="bg"
			>
				{title}
			</Heading>
			<Box
				p="4"
				rounded="card"
				borderWidth="1px"
				borderColor="border"
				bg="bg"
				display={{ lg: "grid" }}
				gridRow={{ lg: `2 / span ${rows}` }}
				gridTemplateRows={{ lg: "subgrid" }}
				css={{ "& [data-paragraph]:not(:last-child)": { pb: "4" } }}
			>
				{children}
			</Box>
		</Box>
	);
}

export function MaskingCorrections({
	projectId,
	review,
	fileType,
}: {
	projectId: string;
	review: DocumentReview;
	fileType: DocumentType;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const frame = useRef<HTMLDivElement>(null);
	const original = useRef<HTMLDivElement>(null);
	const maskedPane = useRef<HTMLDivElement>(null);
	const typeId = useId();
	const [selected, setSelected] = useState<Range | null>(null);
	const [selectedAt, setSelectedAt] = useState<Place | null>(null);
	const [opened, setOpened] = useState<{ mask: Mask; place: Place } | null>(
		null,
	);
	const [type, setType] = useState<EntityType>("PERSON");
	const [removing, setRemoving] = useState<Range | null>(null);
	const [reason, setReason] = useState("");
	const [outcome, setOutcome] = useState<string | null>(null);
	const [isListOpen, setListOpen] = useState(false);
	const listId = useId();
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
			setOpened(null);
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
		const box = frame.current;
		const selection = window.getSelection();
		if (
			pane === null ||
			box === null ||
			selection === null ||
			selection.rangeCount === 0
		) {
			return;
		}
		const range = selection.getRangeAt(0);
		const start = offsetIn(pane, range.startContainer, range.startOffset);
		const end = offsetIn(pane, range.endContainer, range.endOffset);
		if (start === null || end === null || start >= end) {
			setSelected(null);
			return;
		}
		setOpened(null);
		setSelected({ start, end });
		// jsdom has no layout, so a range has no rectangle there.
		const rect =
			typeof range.getBoundingClientRect === "function"
				? range.getBoundingClientRect()
				: NOWHERE;
		setSelectedAt(placeIn(box, rect));
	};

	const masks = masksOf(review.spans);

	// A click (not a drag) on a masked word, or on its placeholder, opens its card there.
	const openCard = (element: Element | null, mask: Mask | undefined) => {
		const box = frame.current;
		if (
			element === null ||
			mask === undefined ||
			box === null ||
			window.getSelection()?.isCollapsed === false
		) {
			return;
		}
		setSelected(null);
		setOpened({ mask, place: placeIn(box, element.getBoundingClientRect()) });
	};

	const openFromOriginal = (target: EventTarget) => {
		const mark = target instanceof Element ? target.closest("mark") : null;
		const offset = Number(
			mark?.querySelector("[data-offset]")?.getAttribute("data-offset") ??
				Number.NaN,
		);
		openCard(
			mark,
			masks.find((m) => m.start <= offset && offset < m.end),
		);
	};

	// Placeholders follow the masks in order, so the k-th is the k-th mask; if the
	// counts ever differ, a placeholder opens nothing rather than the wrong mask.
	const openFromMasked = (target: EventTarget) => {
		const pill =
			target instanceof Element ? target.closest("[data-placeholder]") : null;
		const pills = [
			...(maskedPane.current?.querySelectorAll("[data-placeholder]") ?? []),
		];
		const index = pill === null ? -1 : pills.indexOf(pill);
		openCard(pill, pills.length === masks.length ? masks[index] : undefined);
	};

	const closeCard = useCallback(() => setOpened(null), []);

	// The bar closes as the card does: Esc, or a click outside it and the original.
	const bar = useRef<HTMLDivElement>(null);
	const hasBar = selected !== null;
	useEffect(() => {
		if (!hasBar) {
			return;
		}
		const close = () => setSelected(null);
		const onKey = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				close();
			}
		};
		const onDown = (event: MouseEvent) => {
			const target = event.target instanceof Node ? event.target : null;
			if (
				target !== null &&
				!bar.current?.contains(target) &&
				!original.current?.parentElement?.contains(target)
			) {
				close();
			}
		};
		document.addEventListener("keydown", onKey);
		document.addEventListener("mousedown", onDown);
		return () => {
			document.removeEventListener("keydown", onKey);
			document.removeEventListener("mousedown", onDown);
		};
	}, [hasBar]);

	const unmask = (event: FormEvent) => {
		event.preventDefault();
		if (removing !== null && reason.trim() !== "" && !correct.isPending) {
			correct.mutate({ kind: "unmask", range: removing, reason });
		}
	};

	const allRuns = runs(raw, review.spans);
	const [rawParagraphs, maskedParagraphs] = pairedParagraphs(
		raw,
		review.masked_text,
		fileType,
	);
	// A run that crosses a paragraph break is cut in two, one piece per paragraph.
	const runsIn = (paragraph: Range) =>
		allRuns
			.filter((run) => run.end > paragraph.start && run.start < paragraph.end)
			.map((run) => ({
				...run,
				start: Math.max(run.start, paragraph.start),
				end: Math.min(run.end, paragraph.end),
			}));

	return (
		<Stack gap="5">
			{/* One scrollbar for both panes: they share the grid's rows, so they move
			    together. Zoom scales viewport units, hence the division. */}
			<Box
				as="section"
				ref={frame}
				aria-label="The original and the masked text"
				tabIndex={0}
				maxH={`calc(70vh / ${COMPACT_ZOOM})`}
				overflowY="auto"
				// Holds the placeholders' screen-reader text, which is positioned
				// absolutely; without it that text stretches the page below the frame.
				position="relative"
				pe="2"
			>
				<Grid
					templateColumns={{ base: "1fr", lg: "1fr 1fr" }}
					columnGap="4"
					rowGap={{ base: "4", lg: "0" }}
				>
					<Pane title="Original text" rows={rawParagraphs.length}>
						<Box
							ref={original}
							display="contents"
							onMouseUp={(event) => {
								readSelection();
								openFromOriginal(event.target);
							}}
							onKeyUp={readSelection}
						>
							{rawParagraphs.map((paragraph) => (
								<Text
									key={paragraph.start}
									data-paragraph=""
									whiteSpace="normal"
									overflowWrap="anywhere"
								>
									{runsIn(paragraph).map((run) =>
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
							))}
						</Box>
					</Pane>
					<Pane title="What the model will see" rows={maskedParagraphs.length}>
						<Box
							ref={maskedPane}
							display="contents"
							onMouseUp={(event) => openFromMasked(event.target)}
						>
							{maskedParagraphs.map((paragraph) => (
								<Box
									key={paragraph.start}
									data-paragraph=""
									// Flow like the original beside it: a PDF's line breaks are page wraps.
									css={{ "& > p": { whiteSpace: "normal" } }}
								>
									<MaskedText
										text={review.masked_text.slice(
											paragraph.start,
											paragraph.end,
										)}
									/>
								</Box>
							))}
						</Box>
					</Pane>
				</Grid>
				{selected === null || selectedAt === null ? null : (
					<Flex
						ref={bar}
						role="toolbar"
						aria-label="Mask the selection"
						{...POP_UP}
						p="2"
						gap="2"
						align="center"
						wrap="wrap"
						// Above the selection, unless that would hide it under the frame's top.
						{...(selectedAt.top > 56
							? {
									top: `${selectedAt.top - 6}px`,
									transform: "translateY(-100%)",
								}
							: { top: `${selectedAt.bottom + 6}px` })}
						left={leftWithin(selectedAt, "28rem")}
					>
						<VisuallyHidden>{`Selected: “${quoted(selected)}”`}</VisuallyHidden>
						<chakra.label htmlFor={typeId} fontSize="sm" fontWeight="medium">
							Mask as
						</chakra.label>
						<EntityTypeSelect id={typeId} value={type} onChange={setType} />
						<Button
							size="xs"
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
							size="xs"
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
				)}
				{opened === null ? null : (
					<MaskCard
						key={maskKey(opened.mask)}
						mask={opened.mask}
						words={quoted(opened.mask)}
						place={opened.place}
						isBusy={correct.isPending}
						onUnmask={(why) =>
							correct.mutate({
								kind: "unmask",
								range: opened.mask,
								reason: why,
							})
						}
						onAlwaysMask={() =>
							correct.mutate({
								kind: "mask",
								range: opened.mask,
								type: opened.mask.type,
								isRule: true,
							})
						}
						onClose={closeCard}
					/>
				)}
			</Box>

			<Text fontSize="sm" color="fg.muted">
				Select words in the original to mask them. Click a highlighted word, or
				its placeholder, to change its mask.
			</Text>

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
					Masks
				</Heading>
				{masks.length === 0 ? (
					<Text>Nothing is masked.</Text>
				) : (
					<Flex gap="3" align="center" wrap="wrap">
						<DisclosureButton
							isOpen={isListOpen}
							controls={listId}
							onToggle={() => setListOpen((open) => !open)}
						>
							{isListOpen ? "Hide all masks" : "Show all masks"}
						</DisclosureButton>
					</Flex>
				)}
				{masks.length === 0 || !isListOpen ? null : (
					<Reveal>
						<Stack
							as="ul"
							id={listId}
							aria-label="Masks"
							gap="2"
							listStyle="none"
						>
							{masks.map((mask) => {
								const words = quoted(mask);
								const isRule = mask.sources.some((s) => s.startsWith("rule:"));
								const isRemoving =
									removing?.start === mask.start && removing.end === mask.end;
								return (
									<Stack
										as="li"
										key={maskKey(mask)}
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
					</Reveal>
				)}
			</Stack>
		</Stack>
	);
}
