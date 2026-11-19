/** The account page in a real browser: what jsdom cannot check, such as layout and motion. */

import { expect, test } from "@playwright/test";

test("the account page shows its sections, and Reduce motion stops animations", async ({
	page,
}) => {
	await page.goto("/settings");
	for (const name of ["Signed-in devices", "Sign-in methods", "Appearance"]) {
		await expect(page.getByRole("region", { name })).toBeVisible();
	}

	await page.getByText("Reduce motion").click();

	await expect(page.locator("html")).toHaveAttribute("data-motion", "reduced");
	const durations = await page
		.getByRole("button", { name: "Add a passkey" })
		.evaluate((button) => getComputedStyle(button).transitionDuration);
	for (const duration of durations.split(",")) {
		expect(Number.parseFloat(duration)).toBeLessThan(0.001);
	}
});
