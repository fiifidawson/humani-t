import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return api;
}

describe("All projects (UI_UX_SPEC §7.2, P1: no grouping)", () => {
	it("lists every project the person belongs to, with their own role", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao", "owner");
		api.addProject("Market assessment, Mopti", "viewer");
		renderApp("/projects");

		const table = await screen.findByRole("table", { name: "Your projects" });
		const rows = await within(table).findAllByRole("row");

		expect(rows).toHaveLength(3);
		expect(within(rows[1] ?? table).getByRole("rowheader")).toHaveTextContent(
			"Cash vs vouchers, Gao",
		);
		expect(rows[2]).toHaveTextContent("Viewer");
	});

	it("shows archived projects only when asked", async () => {
		const api = signedIn();
		const archived = api.addProject("Shelter kits, Pemba");
		archived.archived_at = "2026-10-01T00:00:00Z";
		const { user } = renderApp("/projects");
		await screen.findByText("You have no projects yet.");

		await user.click(
			screen.getByRole("checkbox", { name: "Show archived projects" }),
		);

		expect(
			await screen.findByRole("rowheader", { name: /Shelter kits, Pemba/ }),
		).toHaveTextContent("Archived");
	});

	it("has no accessibility violations", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { container } = renderApp("/projects");
		await screen.findByRole("table", { name: "Your projects" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("creating a project", () => {
	it("creates it and opens its workspace", async () => {
		const api = signedIn();
		const { user, router } = renderApp("/projects/new");

		await user.type(
			await screen.findByLabelText("Project name"),
			"Cholera response, Aden",
		);
		await user.click(screen.getByRole("button", { name: "Create project" }));

		await waitFor(() => expect(currentPath(router)).toMatch(/^\/p\//));
		expect(api.projects.map((p) => p.name)).toEqual(["Cholera response, Aden"]);
		expect(
			await screen.findByRole("heading", {
				level: 1,
				name: "Cholera response, Aden",
			}),
		).toBeInTheDocument();
	});

	it("shows the API's reason next to the field it concerns", async () => {
		signedIn();
		const { user } = renderApp("/projects/new");
		const name = await screen.findByLabelText("Project name");

		await user.type(name, "   ");
		await user.click(screen.getByRole("button", { name: "Create project" }));

		expect(
			await screen.findByText(
				"Project name must be 1 to 200 characters on one line.",
			),
		).toBeInTheDocument();
		expect(name).toHaveAttribute("aria-invalid", "true");
		expect(name).toHaveAccessibleDescription(
			expect.stringContaining("Project name must be 1 to 200 characters"),
		);
	});

	it("has no accessibility violations", async () => {
		signedIn();
		const { container } = renderApp("/projects/new");
		await screen.findByLabelText("Project name");

		await expectNoAccessibilityViolations(container);
	});
});
