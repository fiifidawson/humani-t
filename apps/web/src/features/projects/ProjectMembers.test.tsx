import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { PASSWORD } from "../../test/fakeApi";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NAME = "Cash vs vouchers, Gao";
const LAST_OWNER =
	"A project needs at least one owner. Make someone else an owner first.";

function withTeam(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(
		api.addUser({
			email: "amara@example.org",
			displayName: "Amara N.",
			hasTotp: true,
		}),
	);
	const project = api.addProject(NAME, role);
	const kofi = api.addUser({
		email: "kofi@example.org",
		displayName: "Kofi M.",
	});
	const kofiMembership = api.addMember(project, kofi, "reviewer");
	api.addUser({ email: "lina@example.org", displayName: "Lina H." });
	return {
		api,
		project,
		kofi,
		kofiMembership,
		path: `/p/${project.id}/settings`,
	};
}

async function membersList() {
	return within(await screen.findByRole("list", { name: "Members" }));
}

async function memberRow(name: string) {
	const list = await membersList();
	const row = (await list.findAllByRole("listitem")).find((item) =>
		item.textContent?.includes(name),
	);
	if (row === undefined) {
		throw new Error(`No member row for ${name}`);
	}
	return row;
}

async function addLina(user: UserEvent, role = "Viewer") {
	await user.type(
		await screen.findByRole("textbox", { name: "Email" }),
		"lina@example.org",
	);
	await user.selectOptions(
		screen.getByRole("combobox", { name: "Role" }),
		role,
	);
	await user.click(screen.getByRole("button", { name: "Add member" }));
}

async function removeKofi(user: UserEvent) {
	await user.click(
		await screen.findByRole("button", { name: "Remove Kofi M." }),
	);
	const dialog = await screen.findByRole("dialog", {
		name: "Remove Kofi M.?",
	});
	await user.click(within(dialog).getByRole("button", { name: "Remove" }));
}

describe("project members (UI_UX_SPEC §6.14)", () => {
	it("shows owners each member's email, to tell people apart", async () => {
		const { path } = withTeam();
		renderApp(path);

		expect(await memberRow("Amara N.")).toHaveTextContent("amara@example.org");
		expect(await memberRow("Kofi M.")).toHaveTextContent("kofi@example.org");
	});

	it("shows reviewers and viewers names only, never an email", async () => {
		const { path } = withTeam("reviewer");
		renderApp(path);

		const list = await membersList();
		await memberRow("Kofi M.");

		for (const row of list.getAllByRole("listitem")) {
			expect(row.textContent).not.toContain("@");
		}
	});

	it("lists everyone with their name and role, and marks you", async () => {
		const { path } = withTeam();
		renderApp(path);

		const me = await memberRow("Amara N.");
		const kofi = await memberRow("Kofi M.");

		expect(me).toHaveTextContent("You");
		expect(
			within(me).getByRole("combobox", { name: "Role for Amara N." }),
		).toHaveValue("owner");
		expect(
			within(kofi).getByRole("combobox", { name: "Role for Kofi M." }),
		).toHaveValue("reviewer");
	});

	it("shows reviewers and viewers the list without any way to change it", async () => {
		const { path } = withTeam("viewer");
		renderApp(path);

		const kofi = await memberRow("Kofi M.");

		expect(kofi).toHaveTextContent("Reviewer");
		expect(await memberRow("Amara N.")).toHaveTextContent("Viewer");
		expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /Remove/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Add member" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByText("Only owners can change who's in the project."),
		).toBeInTheDocument();
	});

	it("lets an owner add someone by email with a role", async () => {
		const { api, project, path } = withTeam();
		const { user } = renderApp(path);

		await addLina(user);

		const lina = await memberRow("Lina H.");
		expect(
			within(lina).getByRole("combobox", { name: "Role for Lina H." }),
		).toHaveValue("viewer");
		expect(
			api.requestsTo("POST", `/projects/${project.id}/members`)[0]?.body,
		).toEqual({ email: "lina@example.org", role: "viewer" });
		expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue("");
	});

	it("shows the API's message at the email field when no account uses it", async () => {
		const { path } = withTeam();
		const { user } = renderApp(path);

		const email = await screen.findByRole("textbox", { name: "Email" });
		await user.type(email, "nobody@example.org");
		await user.click(screen.getByRole("button", { name: "Add member" }));

		await waitFor(() =>
			expect(email).toHaveAccessibleDescription(
				expect.stringContaining("No active account uses this email address."),
			),
		);
		expect(email).toBeInvalid();
	});

	it("lets an owner change someone's role", async () => {
		const { api, project, kofi, path } = withTeam();
		const { user } = renderApp(path);

		const row = await memberRow("Kofi M.");
		await user.selectOptions(
			within(row).getByRole("combobox", { name: "Role for Kofi M." }),
			"Viewer",
		);

		await waitFor(() =>
			expect(
				api.requestsTo("PATCH", `/projects/${project.id}/members/${kofi.id}`),
			).toHaveLength(1),
		);
		expect(
			api.requestsTo("PATCH", `/projects/${project.id}/members/${kofi.id}`)[0]
				?.body,
		).toEqual({ role: "viewer" });
		await waitFor(() =>
			expect(
				within(row).getByRole("combobox", { name: "Role for Kofi M." }),
			).toHaveValue("viewer"),
		);
	});

	it("lets an owner remove someone, after saying what that means", async () => {
		const { api, project, kofi, path } = withTeam();
		const { user } = renderApp(path);

		await user.click(
			await screen.findByRole("button", { name: "Remove Kofi M." }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Remove Kofi M.?",
		});
		expect(dialog).toHaveTextContent("They lose access to this project");
		await user.click(within(dialog).getByRole("button", { name: "Remove" }));

		await waitFor(() =>
			expect(
				screen.getByRole("list", { name: "Members" }),
			).not.toHaveTextContent("Kofi M."),
		);
		expect(
			api.requestsTo("DELETE", `/projects/${project.id}/members/${kofi.id}`),
		).toHaveLength(1);
	});

	it.each([
		["adding someone", "POST", "/members", addLina],
		[
			"changing a role",
			"PATCH",
			"/members/KOFI",
			async (user: UserEvent) =>
				user.selectOptions(
					await screen.findByRole("combobox", { name: "Role for Kofi M." }),
					"Viewer",
				),
		],
		["removing someone", "DELETE", "/members/KOFI", removeKofi],
	] as const)(
		"asks for the password again before %s when the sign-in is not recent",
		async (_, method, route, act) => {
			const { api, project, kofi, path } = withTeam();
			api.makeSignInStale();
			const { user } = renderApp(path);

			await act(user);

			const reauth = await screen.findByRole("dialog", {
				name: "Sign in again",
			});
			expect(reauth).toHaveTextContent(
				"Changing members needs you to sign in again.",
			);
			await user.type(within(reauth).getByLabelText("Password"), PASSWORD);
			await user.click(within(reauth).getByRole("button", { name: "Confirm" }));

			const requests = () =>
				api.requestsTo(
					method,
					`/projects/${project.id}${route.replace("KOFI", kofi.id)}`,
				);
			await waitFor(() => expect(requests()).toHaveLength(2));
		},
	);

	it("explains why the only owner's role can't change, and locks it", async () => {
		const { path } = withTeam();
		renderApp(path);

		const role = within(await memberRow("Amara N.")).getByRole("combobox", {
			name: "Role for Amara N.",
		});

		expect(role).toBeDisabled();
		expect(role).toHaveAccessibleDescription(LAST_OWNER);
	});

	it("lets an owner step down while there is another owner", async () => {
		const { kofiMembership, path } = withTeam();
		kofiMembership.role = "owner";
		renderApp(path);

		const role = within(await memberRow("Amara N.")).getByRole("combobox", {
			name: "Role for Amara N.",
		});

		expect(role).toBeEnabled();
		expect(screen.queryByText(LAST_OWNER)).not.toBeInTheDocument();
	});

	it("shows the API's explanation when a change would leave no owner", async () => {
		const { kofiMembership, path } = withTeam();
		kofiMembership.role = "owner";
		const { user } = renderApp(path);
		const role = within(await memberRow("Amara N.")).getByRole("combobox", {
			name: "Role for Amara N.",
		});

		// Another owner stepped down after this page loaded.
		kofiMembership.role = "reviewer";
		await user.selectOptions(role, "Reviewer");

		expect(await screen.findByRole("alert")).toHaveTextContent(LAST_OWNER);
	});

	it("has no accessibility violations", async () => {
		const { path } = withTeam();
		const { container } = renderApp(path);
		await memberRow("Kofi M.");

		await expectNoAccessibilityViolations(container);
	});
});
