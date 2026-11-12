import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NEW_PASSWORD = "lantern orchard pebble ferry";
const EXPIRED =
	"This reset link has expired or was already used. Ask for a new one.";

async function setNewPassword(
	user: ReturnType<typeof renderApp>["user"],
	password = NEW_PASSWORD,
) {
	await user.type(await screen.findByLabelText("New password"), password);
	await user.click(screen.getByRole("button", { name: "Set new password" }));
}

describe("asking for a reset link", () => {
	it("is offered from the sign-in page", async () => {
		fakeApi();
		const { user, router } = renderApp("/sign-in");

		await user.click(
			await screen.findByRole("link", { name: "Forgot your password?" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/reset-password"));
		expect(
			await screen.findByRole("heading", {
				level: 1,
				name: "Reset your password",
			}),
		).toBeInTheDocument();
	});

	it("sends the email and answers the same whether or not an account exists", async () => {
		const api = fakeApi();
		const { user } = renderApp("/reset-password");

		await user.type(
			await screen.findByLabelText("Email"),
			"nobody@example.org",
		);
		await user.click(screen.getByRole("button", { name: "Send reset link" }));

		expect(
			await screen.findByText(
				"If an account uses that email, a reset link is on its way. It works once, for 30 minutes.",
			),
		).toBeInTheDocument();
		expect(api.requestsTo("POST", "/auth/password-reset")[0]?.body).toEqual({
			email: "nobody@example.org",
		});
	});
});

describe("setting a new password from the emailed link", () => {
	it("takes the token out of the address bar", async () => {
		const api = fakeApi();
		api.addPasswordReset(api.addUser(), "reset-token-1");
		const { router } = renderApp("/reset-password#token=reset-token-1");

		expect(await screen.findByLabelText("New password")).toBeInTheDocument();
		await waitFor(() => expect(router.state.location.hash).toBe(""));
	});

	it("sets the password and says the second factor is still needed", async () => {
		const api = fakeApi();
		const account = api.addUser();
		api.addPasswordReset(account, "reset-token-1");
		const { user } = renderApp("/reset-password#token=reset-token-1");

		await setNewPassword(user);

		expect(
			await screen.findByText(
				"Your password is changed. You'll still need your passkey or authenticator code to sign in.",
			),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
			"href",
			"/sign-in",
		);
		expect(
			api.requestsTo("POST", "/auth/password-reset/confirm")[0]?.body,
		).toEqual({ token: "reset-token-1", new_password: NEW_PASSWORD });
		expect(account.password).toBe(NEW_PASSWORD);
	});

	it("shows the API's password rules next to the field", async () => {
		const api = fakeApi();
		api.addPasswordReset(api.addUser(), "reset-token-1");
		const { user } = renderApp("/reset-password#token=reset-token-1");

		await setNewPassword(user, "passwordpassword");

		const field = screen.getByLabelText("New password");
		expect(
			await screen.findByText(
				"This password is too common. Choose one that's harder to guess.",
			),
		).toBeInTheDocument();
		expect(field).toHaveAttribute("aria-invalid", "true");
	});

	it("gives one plain message for an expired or used link, and a way to ask again", async () => {
		fakeApi();
		const { user } = renderApp("/reset-password#token=used-long-ago");

		await setNewPassword(user);

		expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Ask for a new link" }),
		);
		expect(await screen.findByLabelText("Email")).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const api = fakeApi();
		api.addPasswordReset(api.addUser(), "reset-token-1");
		const { container } = renderApp("/reset-password#token=reset-token-1");
		await screen.findByLabelText("New password");

		await expectNoAccessibilityViolations(container);
	});
});
