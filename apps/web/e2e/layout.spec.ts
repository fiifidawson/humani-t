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

/** How far the box's centre is from the window's, across and down. */
async function offWindowCentre(page: Page, inner: Locator) {
	const box = await inner.boundingBox();
	const view = page.viewportSize();
	if (box === null || view === null) {
		throw new Error("Nothing to measure");
	}
	return {
		across: Math.abs(box.x + box.width / 2 - view.width / 2),
		down: Math.abs(box.y + box.height / 2 - view.height / 2),
	};
}

test("pop-ups open in the middle of the window, not the top left", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/");
	await page.getByRole("navigation", { name: "Main" }).waitFor();

	await page
		.getByRole("button", { name: /Search/ })
		.first()
		.click();
	const palette = page.getByRole("dialog", { name: "Search and commands" });
	await expect(palette).toBeVisible();
	expect((await offWindowCentre(page, palette)).across).toBeLessThan(2);
	await page.keyboard.press("Escape");
	await expect(palette).toBeHidden();

	await page.keyboard.press("?");
	const shortcuts = page.getByRole("dialog", { name: "Keyboard shortcuts" });
	await expect(shortcuts).toBeVisible();
	const off = await offWindowCentre(page, shortcuts);
	expect(off.across).toBeLessThan(2);
	expect(off.down).toBeLessThan(2);
});

/** Everything inside `dialog` that sticks out past its sides, by its text. */
async function spillingOut(dialog: Locator): Promise<string[]> {
	return dialog.evaluate((box) => {
		const edge = box.getBoundingClientRect();
		return [...box.querySelectorAll<HTMLElement>("*")]
			.filter((element) => {
				const rect = element.getBoundingClientRect();
				return (
					rect.width > 0 &&
					(rect.right > edge.right + 1 || rect.left < edge.left - 1)
				);
			})
			.map((element) => element.textContent?.trim() ?? element.tagName);
	});
}

const LONG_NAME = `Field_assessment_${"x".repeat(80)}_final`;

test("a delete pop-up wraps a long name rather than letting it run out", async ({
	page,
}) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill(LONG_NAME);
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	const project = page.url();

	await page.goto(`${project}/documents`);
	await page.locator("input[type=file]").setInputFiles({
		name: `${LONG_NAME}.txt`,
		mimeType: "text/plain",
		buffer: Buffer.from("Interview notes, 12 March 2024."),
	});
	await page
		.getByRole("button", { name: `Delete D1 ${LONG_NAME}.txt` })
		.click({ timeout: 60_000 });
	const deleteDocument = page.getByRole("dialog");
	await expect(deleteDocument).toBeVisible();
	expect(await spillingOut(deleteDocument)).toEqual([]);
	await page.keyboard.press("Escape");
	await expect(deleteDocument).toBeHidden();

	await page.goto(`${project}/settings`);
	await page.getByRole("button", { name: "Delete project…" }).click();
	const deleteProject = page.getByRole("dialog");
	await expect(deleteProject).toBeVisible();
	expect(await spillingOut(deleteProject)).toEqual([]);
});

/** The header's visible LiGHT wordmark, once the browser has drawn it. */
async function drawnLogo(page: Page): Promise<string | null> {
	const logo = page.getByRole("banner").getByRole("img", { name: "LiGHT" });
	await expect
		.poll(() => logo.evaluate((img: HTMLImageElement) => img.naturalWidth))
		.toBeGreaterThan(0);
	return logo.getAttribute("src");
}

test("LiGHT's wordmark is drawn in its blues, and in white on the dark theme", async ({
	page,
}) => {
	await page.emulateMedia({ colorScheme: "light" });
	await page.goto("/");
	expect(await drawnLogo(page)).toMatch(/light-logo\.svg$/);

	await page.emulateMedia({ colorScheme: "dark" });
	expect(await drawnLogo(page)).toMatch(/light-logo-white\.svg$/);
});
