import { describe, expect, it } from "vitest";
import {
	type DocumentLine,
	pairedBlocks,
	readingBlocks,
	type Shown,
} from "./reading";

const NO_STRUCTURE = { lines: null, pageStarts: [0] };

/** A block as the words it shows, so a test reads like the page. */
function wordsOf(text: string, block: Shown): unknown {
	const cut = ({ start, end }: { start: number; end: number }) =>
		text.slice(start, end);
	switch (block.kind) {
		case "page":
			return `page ${block.number}`;
		case "heading":
			return `h${block.level}: ${cut(block.range)}`;
		case "paragraph":
			return cut(block.range);
		case "list":
			return {
				list: block.items.map(cut),
				hasMarkers: block.hasMarkers,
			};
		case "table":
			return {
				table: block.rows.map((row) =>
					row.map((cell) => (cell === null ? null : cut(cell))),
				),
			};
	}
}

const words = (text: string, blocks: readonly Shown[]) =>
	blocks.map((block) => wordsOf(text, block));

const guessed = (text: string, fileType: "txt" | "pdf" | "docx" = "txt") =>
	words(text, readingBlocks(text, { fileType, ...NO_STRUCTURE }));

describe("guessing headings from the text", () => {
	it("takes a short line before a longer paragraph for a heading", () => {
		const text =
			"Field visit\n\nThe team met the committee at the clinic in the morning.";

		expect(guessed(text)).toEqual([
			"h2: Field visit",
			"The team met the committee at the clinic in the morning.",
		]);
	});

	it("finds a heading after a paragraph in a text file, where each line is a paragraph", () => {
		const text =
			"The committee met twice.\nMethods\nWe interviewed forty households in Gao.";

		expect(guessed(text)[1]).toBe("h2: Methods");
	});

	it.each([
		["it ends in punctuation", "Field visit:\nThe team met the committee."],
		["it starts in lower case", "and then\nThe team met the committee there."],
		["what follows is not a sentence", "Water\nFood and shelter for all"],
		["what follows is shorter", "Clinic staff on duty\nAll left."],
		["it holds a sentence's end", "Done. Next\nThe team met the committee."],
		[
			"it is long",
			"A line that goes on for far too long to be any kind of title at all\nThe team met the committee at the clinic, as planned, and then went home.",
		],
		["it has a list marker", "- Water\nThe team met the committee there."],
	])("never takes a line for a heading when %s", (_why, text) => {
		expect(
			guessed(text).some((b) => typeof b === "string" && b.startsWith("h")),
		).toBe(false);
	});

	it("does not take a printed line that wraps inside a PDF paragraph for a heading", () => {
		const text =
			"It was late.\nThe team met\nKofi Mensah at the clinic in the morning.";

		expect(guessed(text, "pdf")).toEqual([
			"It was late.",
			"The team met\nKofi Mensah at the clinic in the morning.",
		]);
	});

	it("finds a heading at the top of a PDF page, before its paragraph flows on", () => {
		const text =
			"Background\nThe team met Kofi Mensah at the\nclinic in the morning.";

		expect(guessed(text, "pdf")).toEqual([
			"h2: Background",
			"The team met Kofi Mensah at the\nclinic in the morning.",
		]);
	});
});

describe("guessing lists from the text", () => {
	it.each([
		["•", "• Water\n• Food"],
		["-", "- Water\n- Food"],
		["*", "* Water\n* Food"],
		["1.", "1. Water\n2. Food"],
		["a)", "a) Water\nb) Food"],
		["(i)", "(i) Water\n(ii) Food"],
	])(
		"makes lines marked %s a list, keeping the markers in the text",
		(_marker, text) => {
			const [list] = guessed(text);

			expect(list).toEqual({
				list: text.split("\n"),
				hasMarkers: true,
			});
		},
	);

	it("leaves a single marked line a paragraph", () => {
		expect(guessed("1. Introduction\nThe team met twice.")).toEqual([
			"1. Introduction",
			"The team met twice.",
		]);
	});

	it("joins a PDF list item's wrapped lines into the item", () => {
		const text = "• The team met the\ncommittee.\n• Food arrived.";

		expect(guessed(text, "pdf")).toEqual([
			{
				list: ["• The team met the\ncommittee.", "• Food arrived."],
				hasMarkers: true,
			},
		]);
	});
});

describe("page breaks", () => {
	it("puts a divider where each page after the first starts", () => {
		const text = "Page one ends here.\n\nPage two ends here.\n\nThree.";
		const blocks = readingBlocks(text, {
			fileType: "pdf",
			lines: null,
			pageStarts: [0, 21, 42],
		});

		expect(words(text, blocks)).toEqual([
			"Page one ends here.",
			"page 2",
			"Page two ends here.",
			"page 3",
			"Three.",
		]);
	});
});

describe("a Word file's own structure", () => {
	const text = "Field visit\nThe team met.\nWater\nFood\nSite\nHouseholds\nGao";
	const lines: DocumentLine[] = [
		{ kind: "heading", level: 1 },
		{ kind: "paragraph" },
		{ kind: "list_item" },
		{ kind: "list_item" },
		{ kind: "table_cell", table: 0, row: 0, column: 0 },
		{ kind: "table_cell", table: 0, row: 0, column: 1 },
		{ kind: "table_cell", table: 0, row: 1, column: 0 },
	];

	it("is used instead of guessing: headings, lists and tables as the file has them", () => {
		const blocks = readingBlocks(text, {
			fileType: "docx",
			lines,
			pageStarts: [0],
		});

		expect(words(text, blocks)).toEqual([
			"h1: Field visit",
			"The team met.",
			{ list: ["Water", "Food"], hasMarkers: false },
			{
				table: [
					["Site", "Households"],
					["Gao", null],
				],
			},
		]);
	});

	it("keeps a cell's several lines together in its cell", () => {
		const cells = "Gao\nnorth\nMenaka";
		const blocks = readingBlocks(cells, {
			fileType: "docx",
			lines: [
				{ kind: "table_cell", table: 0, row: 0, column: 0 },
				{ kind: "table_cell", table: 0, row: 0, column: 0 },
				{ kind: "table_cell", table: 0, row: 0, column: 1 },
			],
			pageStarts: [0],
		});

		expect(words(cells, blocks)).toEqual([
			{ table: [["Gao\nnorth", "Menaka"]] },
		]);
	});

	it("is guessed over when it does not fit the text", () => {
		const blocks = readingBlocks("Field visit\nThe team met twice.", {
			fileType: "docx",
			lines: [{ kind: "paragraph" }],
			pageStarts: [0],
		});

		expect(blocks.map((b) => b.kind)).toEqual(["heading", "paragraph"]);
	});
});

describe("pairing the original with what the model will see", () => {
	const raw = "Field visit\nMet Kofi Mensah at the clinic.\n• Water\n• Food";
	const masked = "Field visit\nMet [PERSON_1] at the clinic.\n• Water\n• Food";

	it("gives both sides the same blocks from the original's lines", () => {
		const [left, right] = pairedBlocks(raw, masked, {
			fileType: "txt",
			...NO_STRUCTURE,
		});

		expect(words(raw, left)).toEqual([
			"h2: Field visit",
			"Met Kofi Mensah at the clinic.",
			{ list: ["• Water", "• Food"], hasMarkers: true },
		]);
		expect(words(masked, right)).toEqual([
			"h2: Field visit",
			"Met [PERSON_1] at the clinic.",
			{ list: ["• Water", "• Food"], hasMarkers: true },
		]);
	});

	it("falls back to plain paragraphs when a mask joined two lines", () => {
		const joined = "Field [PERSON_1] at the clinic.\n• Water\n• Food";

		const [left, right] = pairedBlocks(raw, joined, {
			fileType: "docx",
			lines: [
				{ kind: "heading", level: 1 },
				{ kind: "paragraph" },
				{ kind: "list_item" },
				{ kind: "list_item" },
			],
			pageStarts: [0],
		});

		expect(left.every((b) => b.kind === "paragraph")).toBe(true);
		expect(right.every((b) => b.kind === "paragraph")).toBe(true);
		expect(words(joined, right)).toEqual([joined]);
		expect(words(raw, left)).toEqual([raw]);
	});
});
