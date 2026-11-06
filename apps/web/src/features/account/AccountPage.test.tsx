import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { PASSWORD } from "../../test/fakeApi";
import {
	installFakeAuthenticator,
	uninstallFakeAuthenticator,
} from "../../test/fakeAuthenticator";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

afterEach(() => {
	uninstallFakeAuthenticator();
});

function signedIn(
	options: { hasTotp?: boolean; hasPasskey?: boolean } = {
		hasTotp: true,
		hasPasskey: true,
	},
) {
	const api = fakeApi();
	const user = api.addUser(options);
	api.signInAs(user);
	api.otherSessions.push({
		id: "s-phone",
		userAgent:
			"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1",
		lastSeen: "2026-11-02T18:00:00Z",
	});
	return { api, user };
}

async function section(name: string) {
	return screen.findByRole("region", { name });
}

describe("signed-in devices (UI_UX_SPEC §6.16)", () => {
	it("lists each device in words, marking this one", async () => {
		signedIn();
		renderApp("/settings");

		const devices = await section("Signed-in devices");
		const items = await within(devices).findAllByRole("listitem");

		expect(items).toHaveLength(2);
		expect(items[0]).toHaveTextContent("Firefox on Windows");
		expect(items[0]).toHaveTextContent("This device");
		expect(items[1]).toHaveTextContent("Safari on iPhone");
	});

	it("signs out one other device", async () => {
		const { api } = signedIn();
		const { user } = renderApp("/settings");
		const devices = await section("Signed-in devices");

		await user.click(
			await within(devices).findByRole("button", {
				name: "Sign out Safari on iPhone",
			}),
		);

		await waitFor(() =>
			expect(within(devices).getAllByRole("listitem")).toHaveLength(1),
		);
		expect(api.otherSessions).toEqual([]);
	});

	it("signs out every other device at once", async () => {
		const { api } = signedIn();
		api.otherSessions.push({
			id: "s-2",
			userAgent: "Mozilla/5.0 (X11; Linux x86_64) Chrome/141.0",
			lastSeen: "2026-11-01T08:00:00Z",
		});
		const { user } = renderApp("/settings");
		const devices = await section("Signed-in devices");

		await user.click(
			await within(devices).findByRole("button", {
				name: "Sign out all other devices",
			}),
		);

		await waitFor(() =>
			expect(within(devices).getAllByRole("listitem")).toHaveLength(1),
		);
		expect(
			api.requestsTo("POST", "/account/sessions/revoke-others"),
		).toHaveLength(1);
	});
});

describe("sign-in methods", () => {
	it("lists passkeys, the authenticator app and the recovery codes left", async () => {
		signedIn();
		renderApp("/settings");

		const methods = await section("Sign-in methods");

		expect(await within(methods).findByText("Work laptop")).toBeInTheDocument();
		expect(within(methods).getByText("Authenticator app")).toBeInTheDocument();
		expect(
			within(methods).getByText("2 recovery codes left"),
		).toBeInTheDocument();
	});

	it("explains, in the API's words, why the last method cannot be removed", async () => {
		const { user: account } = signedIn({ hasPasskey: true });
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.click(
			await within(methods).findByRole("button", {
				name: "Remove Work laptop",
			}),
		);

		expect(
			await within(methods).findByText(
				"You can't remove your last sign-in method. Add another one first.",
			),
		).toBeInTheDocument();
		expect(account.passkeys).toHaveLength(1);
	});

	it("adds a passkey", async () => {
		installFakeAuthenticator();
		const { user: account } = signedIn({ hasTotp: true });
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.type(
			await within(methods).findByLabelText("Name for the new passkey"),
			"Office desktop",
		);
		await user.click(
			within(methods).getByRole("button", { name: "Add a passkey" }),
		);

		expect(
			await within(methods).findByText("Office desktop"),
		).toBeInTheDocument();
		expect(account.passkeys.map((p) => p.name)).toEqual(["Office desktop"]);
	});
});

describe("re-authentication (UI_UX_SPEC §6.1)", () => {
	it("asks for the password in a dialog that says why, then retries the action once", async () => {
		const { api, user: account } = signedIn();
		api.makeSignInStale();
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.click(
			await within(methods).findByRole("button", {
				name: "Remove Work laptop",
			}),
		);
		const dialog = await screen.findByRole("dialog", { name: "Sign in again" });
		expect(dialog).toHaveTextContent(
			"Removing a passkey needs you to sign in again.",
		);
		await user.type(within(dialog).getByLabelText("Password"), PASSWORD);
		await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

		await waitFor(() => expect(account.passkeys).toEqual([]));
		expect(api.requests.filter((r) => r.method === "DELETE")).toHaveLength(2);
		// Chakra's dialog leaves the document once its closing state has rendered.
		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
	});

	it("does nothing when the person cancels", async () => {
		const { api, user: account } = signedIn();
		api.makeSignInStale();
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.click(
			await within(methods).findByRole("button", {
				name: "Remove Work laptop",
			}),
		);
		await user.click(
			within(
				await screen.findByRole("dialog", { name: "Sign in again" }),
			).getByRole("button", { name: "Cancel" }),
		);

		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(account.passkeys).toHaveLength(1);
		expect(within(methods).queryByRole("alert")).not.toBeInTheDocument();
	});

	it("keeps the dialog open with the API's message for a wrong password", async () => {
		const { api } = signedIn();
		api.makeSignInStale();
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.click(
			await within(methods).findByRole("button", {
				name: "Make new recovery codes",
			}),
		);
		const dialog = await screen.findByRole("dialog", { name: "Sign in again" });
		await user.type(
			within(dialog).getByLabelText("Password"),
			"not my password",
		);
		await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

		expect(
			await within(dialog).findByText("The password is incorrect."),
		).toBeInTheDocument();
	});

	it("shows new recovery codes once after re-authenticating", async () => {
		const { api } = signedIn();
		api.makeSignInStale();
		const { user } = renderApp("/settings");
		const methods = await section("Sign-in methods");

		await user.click(
			await within(methods).findByRole("button", {
				name: "Make new recovery codes",
			}),
		);
		const dialog = await screen.findByRole("dialog", { name: "Sign in again" });
		await user.type(within(dialog).getByLabelText("Password"), PASSWORD);
		await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

		expect(await screen.findByText("N0EW-CODE-AAAA-BBBB")).toBeInTheDocument();
	});
});

describe("appearance", () => {
	it("chooses the theme", async () => {
		signedIn();
		const { user } = renderApp("/settings");
		const appearance = await section("Appearance");

		await user.click(within(appearance).getByRole("radio", { name: "Dark" }));

		expect(document.documentElement).toHaveAttribute("data-theme", "dark");
	});
});

describe("the account page", () => {
	it("has no accessibility violations", async () => {
		signedIn();
		const { container } = renderApp("/settings");
		await within(await section("Sign-in methods")).findByText("Work laptop");

		await expectNoAccessibilityViolations(container);
	});
});
