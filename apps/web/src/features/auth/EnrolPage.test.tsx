import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { PASSWORD, TOTP_CODE } from "../../test/fakeApi";
import {
	installFakeAuthenticator,
	uninstallFakeAuthenticator,
} from "../../test/fakeAuthenticator";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

afterEach(() => {
	uninstallFakeAuthenticator();
});

/** A first sign-in: password accepted, no second factor yet. */
async function firstSignIn() {
	fakeApi().addUser();
	const rendered = renderApp("/sign-in");
	await rendered.user.type(
		await screen.findByLabelText("Email"),
		"amara@example.org",
	);
	await rendered.user.type(screen.getByLabelText("Password"), PASSWORD);
	await rendered.user.click(screen.getByRole("button", { name: "Sign in" }));
	await screen.findByRole("heading", {
		level: 1,
		name: "Protect your account",
	});
	return rendered;
}

describe("setting up a second factor on first sign-in (UI_UX_SPEC §6.1)", () => {
	it("keeps an account without one away from the rest of the app", async () => {
		const api = fakeApi();
		const account = api.addUser();
		api.signInAs(account, "enrolment");

		const { router } = renderApp("/projects");

		await waitFor(() => expect(currentPath(router)).toBe("/enrol"));
	});

	it("recommends a passkey, then shows the recovery codes once", async () => {
		installFakeAuthenticator();
		const { user } = await firstSignIn();

		await user.click(screen.getByRole("button", { name: "Add a passkey" }));

		expect(
			await screen.findByRole("heading", { name: "Save your recovery codes" }),
		).toBeInTheDocument();
		expect(screen.getAllByRole("listitem")).toHaveLength(10);
		expect(fakeApi().users[0]?.passkeys).toHaveLength(1);
	});

	it("sets up an authenticator app from its QR code or key", async () => {
		const { user } = await firstSignIn();

		await user.click(
			screen.getByRole("button", { name: "Use an authenticator app instead" }),
		);

		expect(
			await screen.findByRole("img", {
				name: "QR code for your authenticator app",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP"),
		).toBeInTheDocument();
		await user.type(
			screen.getByLabelText("6-digit code from your authenticator app"),
			TOTP_CODE,
		);
		await user.click(screen.getByRole("button", { name: "Confirm" }));
		expect(
			await screen.findByRole("heading", { name: "Save your recovery codes" }),
		).toBeInTheDocument();
	});

	it("shows the API's message when the first code does not match", async () => {
		const { user } = await firstSignIn();
		await user.click(
			screen.getByRole("button", { name: "Use an authenticator app instead" }),
		);

		await user.type(
			await screen.findByLabelText("6-digit code from your authenticator app"),
			"999999",
		);
		await user.click(screen.getByRole("button", { name: "Confirm" }));

		expect(
			await screen.findByText(
				"That code didn't match. Check the time on your phone and try again.",
			),
		).toBeInTheDocument();
	});

	it("will not go on until the person says the codes are saved", async () => {
		installFakeAuthenticator();
		const { user, router } = await firstSignIn();
		await user.click(screen.getByRole("button", { name: "Add a passkey" }));
		const done = await screen.findByRole("button", { name: "Continue" });

		expect(done).toBeDisabled();
		await user.click(
			screen.getByRole("checkbox", { name: "I've saved my recovery codes" }),
		);
		await user.click(done);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("has no accessibility violations", async () => {
		const { container } = await firstSignIn();

		await expectNoAccessibilityViolations(container);
	});
});
