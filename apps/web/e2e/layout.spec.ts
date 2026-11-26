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

/** How many of the shot's pixels differ clearly from `background`, an `rgb(…)` colour. */
function inkedPixels(
	blank: Page,
	shot: Buffer,
	background: string,
): Promise<number> {
	return blank.evaluate(
		async ([src, rgb]) => {
			const [r = 0, g = 0, b = 0] = (rgb.match(/\d+/g) ?? []).map(Number);
			const image = new Image();
			image.src = src;
			await image.decode();
			const canvas = document.createElement("canvas");
			canvas.width = image.width;
			canvas.height = image.height;
			const context = canvas.getContext("2d");
			if (context === null) {
				return 0;
			}
			context.drawImage(image, 0, 0);
			const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
			let count = 0;
			for (let i = 0; i < data.length; i += 4) {
				const difference =
					Math.abs((data[i] ?? 0) - r) +
					Math.abs((data[i + 1] ?? 0) - g) +
					Math.abs((data[i + 2] ?? 0) - b);
				if (difference > 90) {
					count += 1;
				}
			}
			return count;
		},
		[`data:image/png;base64,${shot.toString("base64")}`, background] as const,
	);
}

/** The header's visible LiGHT wordmark, once the browser has drawn it: loaded is not enough. */
async function drawnLogo(page: Page, blank: Page): Promise<string | null> {
	const header = page.getByRole("banner");
	const logo = header.getByRole("img", { name: "LiGHT" });
	// The variant a theme hides is lazy, so it only starts loading once shown.
	await expect
		.poll(
			() =>
				logo.evaluate(
					(img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
				),
			{
				timeout: 15_000,
			},
		)
		.toBe(true);
	// Measured against the header itself: a corner of the logo may be ink, not background.
	const background = await header.evaluate(
		(element) => getComputedStyle(element).backgroundColor,
	);
	await expect(async () => {
		expect(
			await inkedPixels(blank, await logo.screenshot(), background),
		).toBeGreaterThan(100);
	}).toPass({ timeout: 15_000 });
	return logo.getAttribute("src");
}

test("LiGHT's wordmark is drawn in its blues, and in white on the dark theme", async ({
	page,
}) => {
	// A blank page counts the pixels: the app's CSP refuses the screenshot's data: URL.
	const blank = await page.context().newPage();
	await page.bringToFront();
	await page.emulateMedia({ colorScheme: "light" });
	await page.goto("/");
	expect(await drawnLogo(page, blank)).toMatch(/light-logo\.svg$/);

	await page.emulateMedia({ colorScheme: "dark" });
	expect(await drawnLogo(page, blank)).toMatch(/light-logo-white\.svg$/);
	await blank.close();
});

test("your own words stand out from the chat column in both themes", async ({
	page,
}) => {
	await page.goto("/projects/new");
	await page.getByLabel("Project name").fill("Your words");
	await page.getByRole("button", { name: "Create project" }).click();
	await page.waitForURL(/\/p\/[^/]+$/);
	await page
		.getByRole("textbox", { name: "Start a review" })
		.fill("Do cash transfers improve food security?");
	await page.keyboard.press("Enter");
	const yours = page.locator("[data-role='person']").first();
	await expect(yours).toBeVisible({ timeout: 30_000 });

	for (const scheme of ["light", "dark"] as const) {
		await page.emulateMedia({ colorScheme: scheme });
		const colours = await yours.evaluate((card) => {
			let column = card.parentElement;
			while (
				column !== null &&
				getComputedStyle(column).backgroundColor === "rgba(0, 0, 0, 0)"
			) {
				column = column.parentElement;
			}
			return {
				card: getComputedStyle(card).backgroundColor,
				column: column === null ? "" : getComputedStyle(column).backgroundColor,
			};
		});
		expect(colours.card, `${scheme} theme`).not.toBe(colours.column);
	}
});
