import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NAME = "Cash vs vouchers, Gao";
const LAST_OWNER =
	"A project needs at least one owner. Make someone else an owner first.";

function withTeam(role: "owner" | "reviewer" | "viewer" = "reviewer") {
	const api = fakeApi();
	const me = api.addUser({ displayName: "Amara N.", hasTotp: true });
	api.signInAs(me);
	const project = api.addProject(NAME, role);
	const kofi = api.addUser({
		email: "kofi@example.org",
		displayName: "Kofi M.",
	});
	const kofiMembership = api.addMember(project, kofi, "owner");
	return {
		api,
		me,
		project,
		kofiMembership,
		path: `/p/${project.id}/settings`,
	};
}

async function openLeaveDialog(user: UserEvent) {
	await user.click(
		await screen.findByRole("button", { name: "Leave project…" }),
	);
	return screen.findByRole("dialog", { name: `Leave ${NAME}?` });
}

async function railLinkTo(name: string) {
	const rail = await screen.findByRole("navigation", { name: "Main" });
	return within(rail).queryByRole("link", { name: new RegExp(name) });
}

describe("leaving a project (UI_UX_SPEC §6.14)", () => {
	it("says leaving takes access away straight away", async () => {
		const { path } = withTeam();
		const { user } = renderApp(path);

		const dialog = await openLeaveDialog(user);

		expect(dialog).toHaveTextContent("You lose access to this project");
		expect(dialog).toHaveTextContent("straight away");
	});

	it("lets a member leave, then goes to Home without the project in the rail", async () => {
		const { api, me, project, path } = withTeam();
		const { user, router } = renderApp(path);
		await waitFor(async () => expect(await railLinkTo(NAME)).not.toBeNull());

		const dialog = await openLeaveDialog(user);
		await user.click(
			within(dialog).getByRole("button", { name: "Leave project" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		expect(
			api.requestsTo("DELETE", `/projects/${project.id}/members/${me.id}`),
		).toHaveLength(1);
		await waitFor(async () => expect(await railLinkTo(NAME)).toBeNull());
	});

	it("leaves without asking for the password again", async () => {
		const { api, me, project, path } = withTeam("viewer");
		api.makeSignInStale();
		const { user, router } = renderApp(path);

		const dialog = await openLeaveDialog(user);
		await user.click(
			within(dialog).getByRole("button", { name: "Leave project" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		expect(
			screen.queryByRole("dialog", { name: "Sign in again" }),
		).not.toBeInTheDocument();
		expect(
			api.requestsTo("DELETE", `/projects/${project.id}/members/${me.id}`),
		).toHaveLength(1);
	});

	it("lets an owner leave while there is another owner", async () => {
		const { path } = withTeam("owner");
		const { user, router } = renderApp(path);

		const dialog = await openLeaveDialog(user);
		await user.click(
			within(dialog).getByRole("button", { name: "Leave project" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
	});

	it("locks leaving for the only owner, and says why", async () => {
		const { kofiMembership, path } = withTeam("owner");
		kofiMembership.role = "reviewer";
		renderApp(path);

		const leave = await screen.findByRole("button", {
			name: "Leave project…",
		});

		await waitFor(() => expect(leave).toBeDisabled());
		expect(leave).toHaveAccessibleDescription(
			"You're the only owner, so you can't leave. Make someone else an owner first.",
		);
	});

	it("shows the API's refusal in plain words when leaving would leave no owner", async () => {
		const { kofiMembership, path } = withTeam("owner");
		const { user, router } = renderApp(path);
		const dialog = await openLeaveDialog(user);

		// The other owner stepped down after this page loaded.
		kofiMembership.role = "reviewer";
		await user.click(
			within(dialog).getByRole("button", { name: "Leave project" }),
		);

		expect(await within(dialog).findByRole("alert")).toHaveTextContent(
			LAST_OWNER,
		);
		expect(currentPath(router)).toBe(path);
	});

	it("goes to Home when the project turns out to be gone already", async () => {
		const { api, path } = withTeam();
		const { user, router } = renderApp(path);
		const dialog = await openLeaveDialog(user);

		// An owner removed this person after the page loaded.
		api.projects.splice(0, 1);
		await user.click(
			within(dialog).getByRole("button", { name: "Leave project" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		await waitFor(async () => expect(await railLinkTo(NAME)).toBeNull());
	});

	it("has no accessibility violations with the dialog open", async () => {
		const { path } = withTeam();
		const { user } = renderApp(path);
		await openLeaveDialog(user);

		await expectNoAccessibilityViolations(document.body);
	});
});
