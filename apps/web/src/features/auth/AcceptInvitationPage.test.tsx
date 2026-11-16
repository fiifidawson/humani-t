import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const GOOD_PASSWORD = "lantern orchard pebble ferry";

async function fillIn(
	user: ReturnType<typeof renderApp>["user"],
	password = GOOD_PASSWORD,
) {
	await user.type(await screen.findByLabelText("Your name"), "Kofi A.");
	await user.type(screen.getByLabelText("Password"), password);
	await user.click(screen.getByRole("button", { name: "Create account" }));
}

describe("accepting an invitation (UI_UX_SPEC §6.1)", () => {
	it("opens from the emailed link and takes the token out of the address bar", async () => {
		fakeApi().addInvitation("invite-token-1");
		const { router } = renderApp("/accept-invitation#token=invite-token-1");

		expect(
			await screen.findByRole("heading", {
				level: 1,
				name: "Set up your account",
			}),
		).toBeInTheDocument();
		await waitFor(() => expect(router.state.location.hash).toBe(""));
		expect(currentPath(router)).toBe("/accept-invitation");
	});

	it("sends the token in the body and goes on to add a second factor", async () => {
		const api = fakeApi();
		api.addInvitation("invite-token-1");
		const { user, router } = renderApp(
			"/accept-invitation#token=invite-token-1",
		);

		await fillIn(user);

		await waitFor(() => expect(currentPath(router)).toBe("/enrol"));
		const [request] = api.requestsTo("POST", "/auth/register");
		expect(request?.body).toEqual({
			token: "invite-token-1",
			display_name: "Kofi A.",
			password: GOOD_PASSWORD,
		});
		expect(request?.path).not.toContain("invite-token-1");
	});

	it("shows the API's password rules next to the password field", async () => {
		fakeApi().addInvitation("invite-token-1");
		const { user } = renderApp("/accept-invitation#token=invite-token-1");

		await fillIn(user, "short one");

		const password = screen.getByLabelText("Password");
		expect(
			await screen.findByText("Use 15 characters or more."),
		).toBeInTheDocument();
		expect(password).toHaveAttribute("aria-invalid", "true");
		expect(password).toHaveAccessibleDescription(
			expect.stringContaining("Use 15 characters or more."),
		);
	});

	it("says plainly when the link has expired or was already used", async () => {
		fakeApi();
		const { user } = renderApp("/accept-invitation#token=used-long-ago");

		await fillIn(user);

		expect(
			await screen.findByText(
				"This invitation link has expired or was already used. Ask your administrator for a new one.",
			),
		).toBeInTheDocument();
	});

	it("explains a link with no token instead of showing an empty form", async () => {
		fakeApi();
		renderApp("/accept-invitation");

		expect(
			await screen.findByText(
				"This invitation link has expired or was already used. Ask your administrator for a new one.",
			),
		).toBeInTheDocument();
		expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		fakeApi().addInvitation("invite-token-1");
		const { container } = renderApp("/accept-invitation#token=invite-token-1");
		await screen.findByRole("heading", { name: "Set up your account" });

		await expectNoAccessibilityViolations(container);
	});
});
