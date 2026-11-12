import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NAME = "Cash vs vouchers, Gao";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject(NAME, role);
	return { api, project, path: `/p/${project.id}/settings` };
}

async function railLinkTo(name: string) {
	const rail = await screen.findByRole("navigation", { name: "Main" });
	return within(rail).queryByRole("link", { name: new RegExp(name) });
}

describe("archiving a project (UI_UX_SPEC §6.14, §7.7)", () => {
	it("says that archiving hides the project, deletes nothing and keeps it open to members", async () => {
		const { path } = withProject();
		renderApp(path);

		const general = await screen.findByRole("region", { name: "General" });

		await within(general).findByRole("button", { name: "Archive project" });
		expect(general).toHaveTextContent("hides the project from Home");
		expect(general).toHaveTextContent("Nothing is deleted");
		expect(general).toHaveTextContent("members can still open it");
	});

	it("lets an owner archive the project, then shows it archived and offers to unarchive", async () => {
		const { api, project, path } = withProject();
		const { user } = renderApp(path);

		await user.click(
			await screen.findByRole("button", { name: "Archive project" }),
		);

		expect(
			await screen.findByRole("button", { name: "Unarchive project" }),
		).toBeInTheDocument();
		const general = screen.getByRole("region", { name: "General" });
		expect(general).toHaveTextContent("Archived");
		expect(
			api.requestsTo("POST", `/projects/${project.id}/archive`),
		).toHaveLength(1);
		expect(api.projects[0]?.archived_at).not.toBeNull();
	});

	it("drops an archived project from the rail", async () => {
		const { path } = withProject();
		const { user } = renderApp(path);
		await waitFor(async () => expect(await railLinkTo(NAME)).not.toBeNull());

		await user.click(
			await screen.findByRole("button", { name: "Archive project" }),
		);

		await waitFor(async () => expect(await railLinkTo(NAME)).toBeNull());
	});

	it("lets an owner unarchive the project, and it comes back to the rail", async () => {
		const { api, project, path } = withProject();
		project.archived_at = "2026-10-01T00:00:00Z";
		const { user } = renderApp(path);

		await user.click(
			await screen.findByRole("button", { name: "Unarchive project" }),
		);

		expect(
			await screen.findByRole("button", { name: "Archive project" }),
		).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/projects/${project.id}/unarchive`),
		).toHaveLength(1);
		expect(api.projects[0]?.archived_at).toBeNull();
		await waitFor(async () => expect(await railLinkTo(NAME)).not.toBeNull());
	});

	it("tells reviewers and viewers the project is archived, without a way to change it", async () => {
		const { project, path } = withProject("viewer");
		project.archived_at = "2026-10-01T00:00:00Z";
		renderApp(path);

		const general = await screen.findByRole("region", { name: "General" });

		await waitFor(() => expect(general).toHaveTextContent("Archived"));
		expect(
			screen.queryByRole("button", { name: /archive project/i }),
		).not.toBeInTheDocument();
	});

	it("goes to Home when archiving shows the project is no longer there", async () => {
		const { api, path } = withProject();
		const { user, router } = renderApp(path);
		const archive = await screen.findByRole("button", {
			name: "Archive project",
		});

		// Someone removed this person from the project after the page loaded.
		api.projects.splice(0, 1);
		await user.click(archive);

		await waitFor(() => expect(currentPath(router)).toBe("/"));
		await waitFor(async () => expect(await railLinkTo(NAME)).toBeNull());
	});

	it("has no accessibility violations once archived", async () => {
		const { project, path } = withProject();
		project.archived_at = "2026-10-01T00:00:00Z";
		const { container } = renderApp(path);
		await screen.findByRole("button", { name: "Unarchive project" });

		await expectNoAccessibilityViolations(container);
	});
});
