/**
 * Sign in once as the demo administrator, setting up an authenticator app on the
 * way, and keep the session for every other test (playwright.config.ts).
 */

import { expect, test as setup } from "@playwright/test";
import {
	ADMIN_EMAIL,
	ADMIN_PASSWORD,
	SIGNED_IN,
} from "../playwright.config.ts";
import { secretOf, totpCode } from "./totp.ts";

setup(
	"signs in, sets up an authenticator app and saves recovery codes",
	async ({ page }) => {
		await page.goto("/");
		await expect(page).toHaveURL(/\/sign-in/);

		await page.getByLabel("Email").fill(ADMIN_EMAIL);
		await page.getByLabel("Password").fill(ADMIN_PASSWORD);
		await page.getByRole("button", { name: "Sign in" }).click();

		await page
			.getByRole("button", { name: "Use an authenticator app instead" })
			.click();
		const link = page.getByRole("link", {
			name: "Open in an authenticator app on this device",
		});
		const secret = secretOf((await link.getAttribute("href")) ?? "");
		await page
			.getByLabel("6-digit code from your authenticator app")
			.fill(totpCode(secret));
		await page.getByRole("button", { name: "Confirm" }).click();

		await expect(
			page.getByRole("heading", { name: "Save your recovery codes" }),
		).toBeVisible();
		// Chakra draws its own box over the input, so click the label, as a person would.
		await page.getByText("I've saved my recovery codes").click();
		await page.getByRole("button", { name: "Continue" }).click();

		await expect(page).toHaveURL(/localhost:\d+\/$/);
		await page.context().storageState({ path: SIGNED_IN });
	},
);
