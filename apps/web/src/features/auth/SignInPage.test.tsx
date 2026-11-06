import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
	vi.useRealTimers();
});

async function enterPassword(
	user: ReturnType<typeof renderApp>["user"],
	password = PASSWORD,
) {
	await user.type(await screen.findByLabelText("Email"), "amara@example.org");
	await user.type(screen.getByLabelText("Password"), password);
	await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("signing in (UI_UX_SPEC §6.1)", () => {
	it("sends a signed-out visitor to the sign-in page", async () => {
		const { router } = renderApp("/projects");

		expect(
			await screen.findByRole("heading", { level: 1, name: "Sign in" }),
		).toBeInTheDocument();
		expect(currentPath(router)).toBe("/sign-in");
	});

	it("gives one message for every wrong email or password", async () => {
		fakeApi().addUser();
		const { user } = renderApp("/sign-in");

		await enterPassword(user, "not the password at all");

		expect(
			await screen.findByText("That email and password don't match."),
		).toBeInTheDocument();
	});

	it("counts down when there have been too many attempts, and blocks the button until then", async () => {
		fakeApi().addUser({ hasTotp: true });
		fakeApi().throttleNextSignIn = 40;
		const { user } = renderApp("/sign-in");

		await enterPassword(user);

		expect(
			await screen.findByText(/Too many attempts\. Try again in 40 seconds\./),
		).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
	});

	it("goes on to the page that was asked for once signed in", async () => {
		const api = fakeApi();
		api.addUser({ hasTotp: true });
		const { user, router } = renderApp("/projects");
		await enterPassword(user);

		await user.type(
			await screen.findByLabelText("6-digit code from your authenticator app"),
			TOTP_CODE,
		);
		await user.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => expect(currentPath(router)).toBe("/projects"));
	});

	it("never follows a redirect to another site", async () => {
		const api = fakeApi();
		api.addUser({ hasTotp: true });
		const { user, router } = renderApp(
			"/sign-in?redirect=%2F%2Fevil.example%2Fsteal",
		);
		await enterPassword(user);

		await user.type(
			await screen.findByLabelText("6-digit code from your authenticator app"),
			TOTP_CODE,
		);
		await user.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("has no accessibility violations", async () => {
		const { container } = renderApp("/sign-in");
		await screen.findByRole("heading", { name: "Sign in" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("the second factor", () => {
	it("starts the passkey prompt by itself and signs in with it", async () => {
		const authenticator = installFakeAuthenticator();
		fakeApi().addUser({ hasPasskey: true });
		const { user, router } = renderApp("/sign-in");

		await enterPassword(user);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		expect(authenticator.assertions).toHaveLength(1);
	});

	it("offers to try the passkey again after the person cancels it", async () => {
		const authenticator = installFakeAuthenticator();
		authenticator.cancelNext();
		fakeApi().addUser({ hasPasskey: true });
		const { user, router } = renderApp("/sign-in");
		await enterPassword(user);

		await user.click(
			await screen.findByRole("button", { name: "Try the passkey again" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("offers only the methods the account has", async () => {
		fakeApi().addUser({ hasTotp: true });
		const { user } = renderApp("/sign-in");

		await enterPassword(user);

		expect(
			await screen.findByRole("heading", { name: "Confirm it's you" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Use a recovery code" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Use a passkey instead" }),
		).not.toBeInTheDocument();
	});

	it("shows the API's message for a wrong code and lets the person try again", async () => {
		fakeApi().addUser({ hasTotp: true });
		const { user, router } = renderApp("/sign-in");
		await enterPassword(user);
		const code = await screen.findByLabelText(
			"6-digit code from your authenticator app",
		);

		await user.type(code, "000000");
		await user.click(screen.getByRole("button", { name: "Continue" }));
		expect(
			await screen.findByText(
				"That code didn't work. Codes change every 30 seconds.",
			),
		).toBeInTheDocument();

		await user.clear(code);
		await user.type(code, TOTP_CODE);
		await user.click(screen.getByRole("button", { name: "Continue" }));
		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("signs in with a recovery code", async () => {
		fakeApi().addUser({ hasTotp: true });
		const { user, router } = renderApp("/sign-in");
		await enterPassword(user);

		await user.click(
			await screen.findByRole("button", { name: "Use a recovery code" }),
		);
		await user.type(
			screen.getByLabelText("Recovery code"),
			"aaaa-bbbb-cccc-dddd",
		);
		await user.click(screen.getByRole("button", { name: "Continue" }));

		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("sends an account with no second factor yet to set one up", async () => {
		fakeApi().addUser();
		const { user, router } = renderApp("/sign-in");

		await enterPassword(user);

		await waitFor(() => expect(currentPath(router)).toBe("/enrol"));
	});

	it("resumes at the second factor after a reload part-way through", async () => {
		const api = fakeApi();
		api.addUser({ hasTotp: true });
		const first = renderApp("/sign-in");
		await enterPassword(first.user);
		await screen.findByRole("heading", { name: "Confirm it's you" });
		first.unmount();

		renderApp("/");

		const heading = await screen.findByRole("heading", {
			name: "Confirm it's you",
		});
		expect(
			within(heading.closest("main") ?? document.body).getByRole("button", {
				name: "Use a recovery code",
			}),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		fakeApi().addUser({ hasTotp: true });
		const { user, container } = renderApp("/sign-in");
		await enterPassword(user);
		await screen.findByRole("heading", { name: "Confirm it's you" });

		await act(async () => {
			await expectNoAccessibilityViolations(container);
		});
	});
});
