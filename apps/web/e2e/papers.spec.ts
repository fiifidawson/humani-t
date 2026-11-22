/** The screening results in a real browser: layout that jsdom cannot measure. */

import { expect, test } from "@playwright/test";

test("the PRISMA flow keeps every count inside its box at any window width", async ({
	page,
}) => {
	test.setTimeout(180_000);
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("PRISMA widths");
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
		.getByRole("link", { name: "Open papers" })
		.click({ timeout: 120_000 });
	const flow = page.getByRole("region", { name: "PRISMA flow" });
	await expect(flow).toBeVisible();

	const divider = page.getByRole("separator", {
		name: "Resize the conversation",
	});
	for (const width of [1366, 1100, 900, 700, 420]) {
		await page.setViewportSize({ width, height: 768 });
		// The narrowest the canvas gets: the conversation dragged as wide as it goes.
		if (await divider.isVisible()) {
			await divider.focus();
			await page.keyboard.press("End");
		}
		await page.waitForTimeout(200);
		const outside = await flow.evaluate((section) => {
			const box = section.getBoundingClientRect();
			return [...section.querySelectorAll<HTMLElement>("*")]
				.filter((element) => {
					const rect = element.getBoundingClientRect();
					return (
						rect.width > 0 &&
						(rect.right > box.right + 1 || rect.left < box.left - 1)
					);
				})
				.map((element) => element.textContent?.trim() ?? element.tagName);
		});
		expect(outside, `at ${width} px wide`).toEqual([]);
	}
});
