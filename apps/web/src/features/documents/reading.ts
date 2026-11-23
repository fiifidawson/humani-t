/**
 * A document's text laid out for reading: headings, paragraphs, lists, tables and
 * page breaks (ADR 0023). A Word file says what each of its lines is (the API's
 * `lines`); for any other file the layout is guessed from the text, cautiously.
 * Display only: every block is a range of the text, which stays as the API sent it.
 */

import type { components } from "../../api/schema.gen";
import type { DocumentType } from "../../app/documentQueries";

export type DocumentLine = NonNullable<
	components["schemas"]["DocumentReviewOut"]["lines"]
>[number];

export interface Range {
	start: number;
	end: number;
}

/** A block of the page, over ranges of type `P`: lines, then offsets in a text. */
type Block<P> =
	| { kind: "page"; number: number }
	| { kind: "heading"; level: number; range: P }
	| { kind: "paragraph"; range: P }
	| { kind: "list"; hasMarkers: boolean; items: P[] }
	| { kind: "table"; rows: (P | null)[][] };

export type Shown = Block<Range>;

/** Lines `first` to `last`, both included. */
interface Lines {
	first: number;
	last: number;
}

export interface Layout {
	fileType: DocumentType;
	lines: readonly DocumentLine[] | null;
	pageStarts: readonly number[];
}

// A PDF line that ends a sentence ends its paragraph; any other line break in a
// PDF is only where the printed page wrapped.
const ENDS_SENTENCE = /[.!?:;]["”’')\]]?$/;
const HOLDS_SENTENCE_END = /[.!?]["”’')\]]?\s/;
const ENDS_IN_PUNCTUATION = /[.!?:;,…]["”’')\]]?$/;
const LIST_MARKER =
	/^\s*(?:[•●○◦▪■□‣∙·*–-]|\d{1,3}[.)]|[a-z]\)|\((?:[a-z]|[ivx]{1,4}|\d{1,3})\))\s+\S/;
const MAX_HEADING_LENGTH = 60;
// Guessed headings are all one level: the text gives no way to tell levels apart.
const GUESSED_LEVEL = 2;

const PARAGRAPH: DocumentLine = { kind: "paragraph" };

const isBlank = (line: string) => line.trim() === "";
const endsSentence = (line: string) => ENDS_SENTENCE.test(line.trimEnd());

/** The line each page after the first starts on. */
function pageLines(text: string, pageStarts: readonly number[]): Set<number> {
	const lines = new Set<number>();
	for (const start of pageStarts.slice(1)) {
		lines.add(text.slice(0, start).split("\n").length - 1);
	}
	return lines;
}

/**
 * A line is a heading only when it is short, starts a block, has no sentence in it,
 * and the paragraph after it on the same page is longer and ends a sentence. A false
 * heading misleads more than a missed one, so anything less certain is a paragraph.
 */
function isGuessedHeading(
	lines: readonly string[],
	index: number,
	isPdf: boolean,
	pageStarts: Set<number>,
): boolean {
	const line = (lines[index] ?? "").trim();
	if (
		line.length === 0 ||
		line.length > MAX_HEADING_LENGTH ||
		!/\p{L}/u.test(line) ||
		/^\p{Ll}/u.test(line) ||
		ENDS_IN_PUNCTUATION.test(line) ||
		HOLDS_SENTENCE_END.test(line) ||
		LIST_MARKER.test(line)
	) {
		return false;
	}
	const before = lines[index - 1];
	const startsBlock =
		before === undefined ||
		isBlank(before) ||
		pageStarts.has(index) ||
		// In a PDF a line after a sentence can be a paragraph's wrapped first line.
		(!isPdf && endsSentence(before));
	if (!startsBlock) {
		return false;
	}
	let next = index + 1;
	while (next < lines.length && isBlank(lines[next] ?? "")) {
		if (pageStarts.has(next)) {
			return false;
		}
		next += 1;
	}
	if (next >= lines.length || pageStarts.has(next)) {
		return false;
	}
	let length = 0;
	for (let at = next; at < lines.length; at += 1) {
		const text = lines[at] ?? "";
		if (
			isBlank(text) ||
			LIST_MARKER.test(text) ||
			(at > next && pageStarts.has(at))
		) {
			return false;
		}
		length += text.trim().length;
		if (endsSentence(text)) {
			return length > line.length;
		}
		if (!isPdf) {
			return false;
		}
	}
	return false;
}

/** In a PDF, a list item goes on over printed lines until one ends a sentence. */
const continuesItem = (
	lines: readonly string[],
	at: number,
	isPdf: boolean,
): boolean => {
	const line = lines[at] ?? "";
	return (
		isPdf &&
		at > 0 &&
		!isBlank(line) &&
		!LIST_MARKER.test(line) &&
		!endsSentence(lines[at - 1] ?? "")
	);
};

/** What each line is, guessed: lists of two or more marked lines, and headings. */
function guessLines(
	lines: readonly string[],
	isPdf: boolean,
	pageStarts: Set<number>,
): DocumentLine[] {
	const kinds = lines.map((): DocumentLine => PARAGRAPH);
	let at = 0;
	while (at < lines.length) {
		if (!LIST_MARKER.test(lines[at] ?? "")) {
			at += 1;
			continue;
		}
		const items: number[] = [];
		let end = at;
		while (
			end < lines.length &&
			(LIST_MARKER.test(lines[end] ?? "") ||
				continuesItem(lines, end, isPdf)) &&
			(end === at || !pageStarts.has(end))
		) {
			if (LIST_MARKER.test(lines[end] ?? "")) {
				items.push(end);
			}
			end += 1;
		}
		if (items.length >= 2) {
			for (const item of items) {
				kinds[item] = { kind: "list_item" };
			}
		}
		at = end;
	}
	return kinds.map((kind, index) =>
		kind.kind === "paragraph" &&
		isGuessedHeading(lines, index, isPdf, pageStarts)
			? { kind: "heading", level: GUESSED_LEVEL }
			: kind,
	);
}

function tableOf(
	cells: { line: number; row: number; column: number }[],
): (Lines | null)[][] {
	const rows: (Lines | null)[][] = [];
	for (const { line, row, column } of cells) {
		const cellsInRow = rows[row] ?? [];
		rows[row] = cellsInRow;
		const cell = cellsInRow[column];
		cellsInRow[column] =
			cell === undefined || cell === null
				? { first: line, last: line }
				: { first: cell.first, last: line };
	}
	const width = Math.max(1, ...rows.map((row) => row?.length ?? 0));
	return Array.from({ length: rows.length }, (_, r) =>
		Array.from({ length: width }, (_, c) => rows[r]?.[c] ?? null),
	);
}

/** Lines grouped into blocks by kind: what both panes share. */
function blocksOf(
	lines: readonly string[],
	kinds: readonly DocumentLine[],
	isPdf: boolean,
	guessedMarkers: boolean,
	pageStarts: Set<number>,
): Block<Lines>[] {
	const blocks: Block<Lines>[] = [];
	const pageAt = (index: number) =>
		pageStarts.has(index)
			? [...pageStarts].filter((p) => p <= index).length + 1
			: null;
	let at = 0;
	while (at < lines.length) {
		const page = pageAt(at);
		if (page !== null && blocks.at(-1)?.kind !== "page") {
			blocks.push({ kind: "page", number: page });
		}
		const kind = kinds[at] ?? PARAGRAPH;
		const line = lines[at] ?? "";
		if (kind.kind === "table_cell") {
			const cells: { line: number; row: number; column: number }[] = [];
			let end = at;
			for (
				let cell = kinds[end];
				cell?.kind === "table_cell";
				cell = kinds[end]
			) {
				if (cell.table !== kind.table) {
					break;
				}
				cells.push({ line: end, row: cell.row, column: cell.column });
				end += 1;
			}
			blocks.push({ kind: "table", rows: tableOf(cells) });
			at = end;
		} else if (isBlank(line)) {
			at += 1;
		} else if (kind.kind === "heading") {
			blocks.push({
				kind: "heading",
				level: kind.level,
				range: { first: at, last: at },
			});
			at += 1;
		} else if (kind.kind === "list_item") {
			const items: Lines[] = [];
			let end = at;
			while (end < lines.length && (end === at || pageAt(end) === null)) {
				const item = kinds[end];
				const last = items.at(-1);
				if (item?.kind === "list_item" && !isBlank(lines[end] ?? "")) {
					items.push({ first: end, last: end });
				} else if (
					last !== undefined &&
					item?.kind === "paragraph" &&
					continuesItem(lines, end, isPdf)
				) {
					last.last = end;
				} else {
					break;
				}
				end += 1;
			}
			blocks.push({ kind: "list", hasMarkers: guessedMarkers, items });
			at = end;
		} else {
			let end = at;
			// A PDF paragraph flows on over plain lines until one ends a sentence.
			while (
				isPdf &&
				!endsSentence(lines[end] ?? "") &&
				end + 1 < lines.length &&
				!isBlank(lines[end + 1] ?? "") &&
				kinds[end + 1]?.kind === "paragraph" &&
				pageAt(end + 1) === null
			) {
				end += 1;
			}
			blocks.push({ kind: "paragraph", range: { first: at, last: end } });
			at = end + 1;
		}
	}
	return blocks;
}

function layoutLines(text: string, layout: Layout): Block<Lines>[] {
	const lines = text.split("\n");
	const isPdf = layout.fileType === "pdf";
	const pageStarts = pageLines(text, layout.pageStarts);
	const isFromFile =
		layout.lines !== null && layout.lines.length === lines.length;
	const kinds =
		isFromFile && layout.lines !== null
			? layout.lines
			: guessLines(lines, isPdf, pageStarts);
	return blocksOf(lines, kinds, isPdf, !isFromFile, pageStarts);
}

function mapRanges<P, Q>(block: Block<P>, to: (range: P) => Q): Block<Q> {
	switch (block.kind) {
		case "page":
			return block;
		case "heading":
		case "paragraph":
			return { ...block, range: to(block.range) };
		case "list":
			return { ...block, items: block.items.map(to) };
		case "table":
			return {
				kind: "table",
				rows: block.rows.map((row) =>
					row.map((cell) => (cell === null ? null : to(cell))),
				),
			};
	}
}

/** Line ranges as offsets in `text`, which has the same lines as the one laid out. */
function inText(text: string, blocks: readonly Block<Lines>[]): Shown[] {
	const lines = text.split("\n");
	const starts: number[] = [];
	let offset = 0;
	for (const line of lines) {
		starts.push(offset);
		offset += line.length + 1;
	}
	const range = ({ first, last }: Lines): Range => ({
		start: starts[first] ?? 0,
		end: (starts[last] ?? 0) + (lines[last]?.length ?? 0),
	});
	return blocks.map((block) => mapRanges(block, range));
}

/** One text's blocks, as an approved document's masked text is shown. */
export function readingBlocks(text: string, layout: Layout): Shown[] {
	return inText(text, layoutLines(text, layout));
}

/**
 * `text` in paragraphs, as the document showed them: a Word, text or Markdown
 * file has one per line; a PDF's lines flow together until a blank line or the
 * end of a sentence. Blank lines are left out.
 */
function paragraphs(text: string, fileType: DocumentType): Shown[] {
	const found: Shown[] = [];
	let start: number | null = null;
	let end = 0;
	let offset = 0;
	const close = (from: number) =>
		found.push({ kind: "paragraph", range: { start: from, end } });
	for (const line of text.split("\n")) {
		if (isBlank(line)) {
			if (start !== null) {
				close(start);
			}
			start = null;
		} else {
			start ??= offset;
			end = offset + line.length;
			if (fileType !== "pdf" || endsSentence(line)) {
				close(start);
				start = null;
			}
		}
		offset += line.length + 1;
	}
	if (start !== null) {
		close(start);
	}
	return found.length > 0
		? found
		: [{ kind: "paragraph", range: { start: 0, end: text.length } }];
}

/**
 * The original's and the masked text's blocks, side by side. Both come from the
 * original's lines, which masking keeps; where a mask joined two lines they cannot,
 * and each side falls back to plain paragraphs, or to one block if even those differ.
 */
export function pairedBlocks(
	raw: string,
	masked: string,
	layout: Layout,
): [Shown[], Shown[]] {
	if (raw.split("\n").length === masked.split("\n").length) {
		const blocks = layoutLines(raw, layout);
		return [inText(raw, blocks), inText(masked, blocks)];
	}
	const left = paragraphs(raw, layout.fileType);
	const right = paragraphs(masked, layout.fileType);
	const whole = (text: string): Shown[] => [
		{ kind: "paragraph", range: { start: 0, end: text.length } },
	];
	return left.length === right.length
		? [left, right]
		: [whole(raw), whole(masked)];
}
