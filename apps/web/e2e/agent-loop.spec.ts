/** The brief's agent loop in a real browser: layout that jsdom cannot measure. */

import { expect, type Locator, type Page, test } from "@playwright/test";

/** Every element of `area` that runs past its sides, by its words. */
function outside(area: Locator): Promise<string[]> {
	return area.evaluate((root) => {
		const box = root.getBoundingClientRect();
		return [...root.querySelectorAll<HTMLElement>("*")]
			.filter((element) => {
				const rect = element.getBoundingClientRect();
				return (
					rect.width > 0 &&
					(rect.right > box.right + 1 || rect.left < box.left - 1)
				);
			})
			.map((element) => element.textContent?.trim() ?? element.tagName);
	});
}

const pageOverflows = (page: Page) =>
	page.evaluate(
		() =>
			document.documentElement.scrollWidth >
			document.documentElement.clientWidth,
	);

test("the agent loop fits the canvas, from a phone to a wide screen", async ({
	page,
}) => {
	test.setTimeout(240_000);
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Agent loop widths");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page
		.getByRole("textbox", { name: "Start a review" })
		.fill("Do cash transfers improve food security in humanitarian settings?");
	await page.keyboard.press("Enter");
	await page.getByRole("button", { name: "Skip all and draft now" }).click();
	await page
		.getByRole("button", { name: "Accept and search" })
		.click({ timeout: 60_000 });
	await page
		.getByRole("link", { name: "How this was made" })
		.first()
		.click({ timeout: 180_000 });
	const canvas = page.getByRole("region", { name: "How this brief was made" });
	const picker = canvas.getByRole("group", { name: "Round" });
	await expect(picker).toBeVisible({ timeout: 30_000 });
	// The diagram follows the picker. It is hidden from screen readers, so no role finds it.
	const loop = picker.locator("xpath=following-sibling::*[1]");

	for (const width of [1366, 360]) {
		await page.setViewportSize({ width, height: 768 });
		await page.waitForTimeout(300);
		expect(await outside(loop), `at ${width} px wide`).toEqual([]);
		expect(await outside(canvas), `at ${width} px wide`).toEqual([]);
		expect(await pageOverflows(page), `at ${width} px wide`).toBe(false);
	}
});
