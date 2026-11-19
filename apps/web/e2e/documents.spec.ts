/** The privacy review in a real browser: the comparison must line up, which jsdom cannot measure. */

import { expect, type Locator, test } from "@playwright/test";

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
