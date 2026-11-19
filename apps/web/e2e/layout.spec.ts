/** Layout that only a real browser can check: jsdom does no layout at all. */

import { expect, type Locator, type Page, test } from "@playwright/test";

/** How far `inner` is from the centre of the main area, in pixels. */
async function offCentre(page: Page, inner: Locator): Promise<number> {
	const main = await page.getByRole("main").boundingBox();
	const box = await inner.boundingBox();
	if (main === null || box === null) {
		throw new Error("Nothing to measure");
	}
	const left = box.x - main.x;
	const right = main.x + main.width - (box.x + box.width);
	return Math.abs(left - right);
}

test("a project's settings sit in the middle of the page, as every other page does", async ({
	page,
}) => {
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Centred settings");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);

	await page.goto(`${page.url()}/settings`);

	// A scrollbar can take up to about 20 px from one side.
	expect(
		await offCentre(page, page.getByRole("region", { name: "General" })),
	).toBeLessThan(24);
});

test("adding a passkey keeps its button to the size of its words", async ({
	page,
}) => {
	await page.goto("/settings");
	const add = page.getByRole("button", { name: "Add a passkey" });
	const name = page.getByLabel("Name for the new passkey");

	const button = await add.boundingBox();
	const field = await name.boundingBox();

	expect(button?.width ?? 0).toBeLessThan((field?.width ?? 0) / 2);
});
