import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { PASSWORD } from "../../test/fakeApi";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NAME = "Cash vs vouchers, Gao";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject(NAME, role);
	return { api, project, path: `/p/${project.id}/settings` };
}

async function openDeleteDialog(path: string) {
	const view = renderApp(path);
	await view.user.click(
		await screen.findByRole("button", { name: "Delete project…" }),
	);
	const dialog = await screen.findByRole("dialog", { name: `Delete ${NAME}?` });
	return { ...view, dialog };
}

describe("project settings (UI_UX_SPEC §6.14)", () => {
	it("opens from the workspace header", async () => {
		const { project } = withProject();
		const { user, router } = renderApp(`/p/${project.id}`);

		await user.click(
			await screen.findByRole("link", { name: "Project settings" }),
		);

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/settings`),
		);
		expect(
			await screen.findByRole("heading", { name: "Danger zone" }),
		).toBeInTheDocument();
	});

	it("says what deleting destroys, and that it cannot be undone", async () => {
		const { path } = withProject();
		const { dialog } = await openDeleteDialog(path);

		expect(dialog).toHaveTextContent("papers");
		expect(dialog).toHaveTextContent("briefs");
		expect(dialog).toHaveTextContent("can't be undone");
	});

	it("deletes only once the project's name is typed exactly", async () => {
		const { api, project, path } = withProject();
		const { user, router, dialog } = await openDeleteDialog(path);
		const confirm = within(dialog).getByRole("button", {
			name: "Delete project",
		});
		const name = within(dialog).getByRole("textbox", {
			name: "Type the project's name to confirm",
		});

		await user.type(name, "Cash vs vouchers");
		expect(confirm).toBeDisabled();

		await user.type(name, ", Gao");
		expect(confirm).toBeEnabled();
		await user.click(confirm);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		expect(api.requestsTo("DELETE", `/projects/${project.id}`)).toHaveLength(1);
		expect(
			screen.queryByRole("link", { name: new RegExp(NAME) }),
		).not.toBeInTheDocument();
	});

	it("asks for the password again when the sign-in is not recent, then deletes", async () => {
		const { api, project, path } = withProject();
		api.makeSignInStale();
		const { user, router, dialog } = await openDeleteDialog(path);
		await user.type(
			within(dialog).getByRole("textbox", {
				name: "Type the project's name to confirm",
			}),
			NAME,
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Delete project" }),
		);

		const reauth = await screen.findByRole("dialog", { name: "Sign in again" });
		expect(reauth).toHaveTextContent(
			"Deleting a project needs you to sign in again.",
		);
		await user.type(within(reauth).getByLabelText("Password"), PASSWORD);
		await user.click(within(reauth).getByRole("button", { name: "Confirm" }));

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		expect(api.requestsTo("DELETE", `/projects/${project.id}`)).toHaveLength(2);
	});

	it("offers deleting to owners only", async () => {
		const { path } = withProject("reviewer");
		renderApp(path);

		expect(
			await screen.findByText("Only owners can delete a project."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Delete project…" }),
		).not.toBeInTheDocument();
	});

	it("lets an owner rename the project, and the rail shows the new name", async () => {
		const { api, project, path } = withProject();
		const { user } = renderApp(path);

		const name = await screen.findByRole("textbox", { name: "Project name" });
		expect(name).toHaveValue(NAME);
		await user.clear(name);
		await user.type(name, "Cholera response, Aden");
		await user.click(screen.getByRole("button", { name: "Save name" }));

		expect(
			(await screen.findAllByRole("link", { name: /Cholera response, Aden/ }))
				.length,
		).toBeGreaterThan(0);
		expect(api.requestsTo("PATCH", `/projects/${project.id}`)[0]?.body).toEqual(
			{ name: "Cholera response, Aden" },
		);
		expect(api.projects[0]?.name).toBe("Cholera response, Aden");
	});

	it("shows the API's message at the name field when it refuses the name", async () => {
		const { path } = withProject();
		const { user } = renderApp(path);

		const name = await screen.findByRole("textbox", { name: "Project name" });
		await user.clear(name);
		await user.type(name, "   ");
		await user.click(screen.getByRole("button", { name: "Save name" }));

		await waitFor(() =>
			expect(name).toHaveAccessibleDescription(
				expect.stringContaining("Project.name must not be blank"),
			),
		);
		expect(name).toBeInvalid();
	});

	it("offers renaming to owners only", async () => {
		const { path } = withProject("reviewer");
		renderApp(path);

		expect(
			await screen.findByText("Only owners can rename the project."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("textbox", { name: "Project name" }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path } = withProject();
		const { container } = renderApp(path);
		await screen.findByRole("heading", { name: "Danger zone" });

		await expectNoAccessibilityViolations(container);
	});
});
