/** The privacy review in a real browser: the comparison must line up, which jsdom cannot measure. */

import { expect, type Locator, test } from "@playwright/test";
import { docx, paragraph, table } from "./docx.ts";

const REPORT = [
	"Interview notes, 12 March 2024.",
	"Kofi Mensah (kofi.mensah@example.org, +223 76 12 34 56) said the cash arrived late, and that the queue at the distribution point was long enough that some families left before their turn came.",
	"Amina Yusuf runs the clinic. Her patient record MRN 448812 was shared by mistake.",
	"The committee met twice.",
].join("\n\n");

async function tops(pane: Locator): Promise<number[]> {
	return pane
		.locator("[data-paragraph]")
		.evaluateAll((paragraphs) =>
			paragraphs.map((p) => Math.round(p.getBoundingClientRect().top)),
		);
}

test("the original and what the model will see line up paragraph by paragraph", async ({
	page,
}) => {
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Comparison");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page.goto(`${page.url()}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: "field_report.txt",
		mimeType: "text/plain",
		buffer: Buffer.from(REPORT),
	});
	await page
		.getByRole("button", { name: "Review D1 field_report.txt" })
		.click({ timeout: 60_000 });

	const original = page.getByRole("region", { name: "Original text" });
	const masked = page.getByRole("region", { name: "What the model will see" });
	const left = await tops(original);
	const right = await tops(masked);

	expect(left).toHaveLength(4);
	expect(right).toEqual(left);
	// A placeholder sits against the punctuation after it, as the word it replaced did.
	const gap = await masked
		.getByText("Email 1", { exact: true })
		.evaluate((element) => {
			const tag = element.parentElement;
			const next = tag?.nextSibling;
			if (
				tag === null ||
				tag === undefined ||
				next === null ||
				next === undefined
			) {
				return Number.POSITIVE_INFINITY;
			}
			const range = document.createRange();
			range.setStart(next, 0);
			range.setEnd(next, 1);
			return (
				range.getBoundingClientRect().left - tag.getBoundingClientRect().right
			);
		});
	expect(gap).toBeLessThan(1);
});

test("a long review scrolls in one place, not two, on a laptop screen", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("One scrollbar");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page.goto(`${page.url()}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: "field_report.txt",
		mimeType: "text/plain",
		buffer: Buffer.from(REPORT),
	});
	await page
		.getByRole("button", { name: "Review D1 field_report.txt" })
		.click({ timeout: 60_000 });
	await expect(
		page.getByRole("region", { name: "Original text" }),
	).toBeVisible();

	const scrolling = await page.evaluate(
		() =>
			[...document.querySelectorAll<HTMLElement>("body, body *")].filter(
				(element) => {
					const overflow = getComputedStyle(element).overflowY;
					return (
						/(auto|scroll)/.test(overflow) &&
						element.scrollHeight > element.clientHeight + 1 &&
						!element.closest("nav")
					);
				},
			).length,
	);
	expect(scrolling).toBe(1);
});

test("a long document stays in a frame shorter than the window, both sides scrolling together", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Long document");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page.goto(`${page.url()}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: "long_report.txt",
		mimeType: "text/plain",
		buffer: Buffer.from(Array.from({ length: 12 }, () => REPORT).join("\n\n")),
	});
	await page
		.getByRole("button", { name: "Review D1 long_report.txt" })
		.click({ timeout: 60_000 });

	const frame = page.getByRole("region", {
		name: "The original and the masked text",
	});
	const original = frame.getByRole("region", { name: "Original text" });
	const masked = frame.getByRole("region", { name: "What the model will see" });
	await expect(original).toBeVisible();
	const windowHeight = await page.evaluate(() => window.innerHeight);
	const box = await frame.boundingBox();
	expect(box?.height ?? Number.POSITIVE_INFINITY).toBeLessThan(
		windowHeight * 0.75,
	);

	await frame.evaluate((element) => {
		element.scrollTop = 600;
	});
	expect(await tops(masked)).toEqual(await tops(original));
	const heading = frame.getByRole("heading", { name: "Original text" });
	const headingTop = (await heading.boundingBox())?.y ?? 0;
	expect(headingTop).toBeGreaterThanOrEqual((box?.y ?? 0) - 1);
	expect(await frame.evaluate((element) => element.scrollTop)).toBe(600);

	// Scrolled to its end, the page stops at the review: nothing in the frame stretches it.
	await page.evaluate(() => {
		for (const element of document.querySelectorAll<HTMLElement>("main *")) {
			if (/(auto|scroll)/.test(getComputedStyle(element).overflowY)) {
				element.scrollTop = element.scrollHeight;
			}
		}
	});
	await expect(page.getByRole("button", { name: "Approve" })).toBeInViewport();
});

test("the mask bar and a mask's card open right at the words, even after scrolling", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Masking on the text");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page.goto(`${page.url()}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: "long_report.txt",
		mimeType: "text/plain",
		buffer: Buffer.from(Array.from({ length: 6 }, () => REPORT).join("\n\n")),
	});
	await page
		.getByRole("button", { name: "Review D1 long_report.txt" })
		.click({ timeout: 60_000 });
	const frame = page.getByRole("region", {
		name: "The original and the masked text",
	});
	const original = frame.getByRole("region", { name: "Original text" });
	await expect(original).toBeVisible();
	await frame.scrollIntoViewIfNeeded();
	await frame.evaluate((element) => {
		element.scrollTop = 300;
	});

	// Select words that are on screen, as a mouse drag would.
	const selected = await original.evaluate((pane) => {
		const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT);
		for (
			let node = walker.nextNode();
			node !== null;
			node = walker.nextNode()
		) {
			const at = node.textContent?.indexOf("the queue") ?? -1;
			const holder = node.parentElement;
			const top = holder?.getBoundingClientRect().top ?? 0;
			if (
				at >= 0 &&
				holder !== null &&
				top > 200 &&
				top < window.innerHeight - 120
			) {
				const range = document.createRange();
				range.setStart(node, at);
				range.setEnd(node, at + "the queue".length);
				window.getSelection()?.removeAllRanges();
				window.getSelection()?.addRange(range);
				holder.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
				const rect = range.getBoundingClientRect();
				return { top: rect.top, bottom: rect.bottom };
			}
		}
		throw new Error("No visible text to select");
	});
	const bar = page.getByRole("toolbar", { name: "Mask the selection" });
	await expect(bar).toBeInViewport();
	const barBox = await bar.boundingBox();
	const barGap = Math.min(
		Math.abs((barBox?.y ?? 0) + (barBox?.height ?? 0) - selected.top),
		Math.abs((barBox?.y ?? 0) - selected.bottom),
	);
	expect(barGap).toBeLessThan(16);
	await page.keyboard.press("Escape");
	await expect(bar).toBeHidden();

	// Click a masked email that is on screen: its card opens just below it. An email,
	// not a name: CI's API has no name detector (the optional `ner` extra), only patterns.
	const email = original.locator("mark", {
		hasText: "kofi.mensah@example.org",
	});
	let target = email.first();
	for (const candidate of await email.all()) {
		const box = await candidate.boundingBox();
		if (box !== null && box.y > 160 && box.y < 560) {
			target = candidate;
			break;
		}
	}
	await target.click();
	const card = page.getByRole("dialog", {
		name: "Mask: kofi.mensah@example.org",
	});
	await expect(card).toBeInViewport();
	const markBox = await target.boundingBox();
	const cardBox = await card.boundingBox();
	expect(
		Math.abs((cardBox?.y ?? 0) - ((markBox?.y ?? 0) + (markBox?.height ?? 0))),
	).toBeLessThan(16);
});

test("a structured document's blocks line up across the panes, and a wide table scrolls in its own row", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	const wide = Array.from(
		{ length: 6 },
		(_, i) => `Households_registered_in_district_${i}`,
	);
	const body = [
		paragraph("Field visit", { style: "Title" }),
		paragraph("Background", { style: "Heading1" }),
		...REPORT.split("\n\n").map((text) => paragraph(text)),
		paragraph("Water", { isNumbered: true }),
		paragraph("Food", { isNumbered: true }),
		table([wide, wide.map((_, i) => `${i * 40}`)]),
		...Array.from({ length: 4 }, () =>
			REPORT.split("\n\n").map((text) => paragraph(text)),
		).flat(),
	].join("");
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Structured document");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page.goto(`${page.url()}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: "visit.docx",
		mimeType:
			"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		buffer: docx(body),
	});
	await page
		.getByRole("button", { name: "Review D1 visit.docx" })
		.click({ timeout: 60_000 });

	const frame = page.getByRole("region", {
		name: "The original and the masked text",
	});
	const original = frame.getByRole("region", { name: "Original text" });
	const masked = frame.getByRole("region", { name: "What the model will see" });
	for (const pane of [original, masked]) {
		await expect(
			pane.getByRole("heading", { name: "Background" }),
		).toBeVisible();
		await expect(pane.getByRole("table")).toBeVisible();
	}
	expect(await tops(masked)).toEqual(await tops(original));

	// The table is wider than its pane: it scrolls sideways in its row, the frame does not.
	const holder = original.getByRole("table").locator("xpath=..");
	const sideways = await holder.evaluate((element) => ({
		scrolls: element.scrollWidth > element.clientWidth + 1,
		frameScrolls: (() => {
			const box = element.closest<HTMLElement>(
				'[aria-label="The original and the masked text"]',
			);
			return box === null ? true : box.scrollWidth > box.clientWidth + 1;
		})(),
	}));
	expect(sideways).toEqual({ scrolls: true, frameScrolls: false });

	// The masked table keeps the same widths: no column is squeezed into fragments.
	const widths = (pane: typeof original) =>
		pane
			.getByRole("columnheader")
			.evaluateAll((cells) => cells.map((cell) => cell.clientWidth));
	expect(await widths(masked)).toEqual(await widths(original));

	// The table's row never scrolls up and down: apart from the review's own frame
	// (this document is longer than it), the page is the only vertical scroller.
	const scrolling = await page.evaluate(
		() =>
			[...document.querySelectorAll<HTMLElement>("body, body *")].filter(
				(element) => {
					const overflow = getComputedStyle(element).overflowY;
					return (
						/(auto|scroll)/.test(overflow) &&
						element.scrollHeight > element.clientHeight + 1 &&
						!element.closest("nav") &&
						element.getAttribute("aria-label") !==
							"The original and the masked text"
					);
				},
			).length,
	);
	expect(scrolling).toBe(1);

	await page.evaluate(() => {
		for (const element of document.querySelectorAll<HTMLElement>("main *")) {
			if (/(auto|scroll)/.test(getComputedStyle(element).overflowY)) {
				element.scrollTop = element.scrollHeight;
			}
		}
	});
	await expect(page.getByRole("button", { name: "Approve" })).toBeInViewport();
});
