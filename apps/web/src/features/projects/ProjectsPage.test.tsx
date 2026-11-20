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

const LIST = "/projects?view=list&group=none";

/** The page itself: the rail lists recent projects by the same names. */
const page = () => within(screen.getByRole("main"));

describe("All projects (UI_UX_SPEC §7.2)", () => {
	it("lists every project the person belongs to, with their own role", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao", "owner");
		api.addProject("Market assessment, Mopti", "viewer");
		renderApp(LIST);

		const table = await screen.findByRole("table", { name: "Your projects" });
		const rows = await within(table).findAllByRole("row");

		expect(rows).toHaveLength(3);
		expect(
			within(table).getByRole("rowheader", { name: /Cash vs vouchers, Gao/ }),
		).toBeInTheDocument();
		expect(
			within(table).getByRole("rowheader", {
				name: /Market assessment, Mopti/,
			}),
		).toBeInTheDocument();
	});

	it("shows the list's columns", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		renderApp(LIST);

		const table = await screen.findByRole("table", { name: "Your projects" });

		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual([
			"Select",
			"Name",
			"Collection",
			"Tags",
			"Status",
			"Last activity",
			"Members",
			"Relevant papers",
			"Brief",
		]);
	});

	it("groups cards by collection by default, with projects in none last", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		api.addProject("Biometric registration");
		renderApp("/projects");

		const groups = await screen.findAllByRole("button", {
			name: / · 1 project$/,
		});

		expect(groups.map((g) => g.textContent)).toEqual([
			"Sahel 2026 · 1 project",
			"Not in a collection · 1 project",
		]);
		expect(groups[0]).toHaveAttribute("aria-expanded", "true");
	});

	it("folds a group away and back", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp("/projects");
		const group = await screen.findByRole("button", {
			name: "Not in a collection · 1 project",
		});

		await user.click(group);

		expect(group).toHaveAttribute("aria-expanded", "false");
		expect(
			page().queryByRole("link", { name: "Cash vs vouchers, Gao" }),
		).not.toBeInTheDocument();
	});

	it("shows a project with several tags under each tag, marked where else it is", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao").tags = ["Mali", "cash"];
		renderApp("/projects?group=tag");

		const mali = await screen.findByRole("region", { name: /^Mali/ });
		const cash = screen.getByRole("region", { name: /^cash/ });

		expect(within(mali).getByText("Also in cash")).toBeInTheDocument();
		expect(within(cash).getByText("Also in Mali")).toBeInTheDocument();
	});

	it("keeps a filter in the URL and asks the API for it", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao").status = "failed";
		api.addProject("Market assessment, Mopti");
		const { user, router } = renderApp("/projects?group=none");
		await within(await screen.findByRole("main")).findByRole("link", {
			name: "Market assessment, Mopti",
		});

		await user.selectOptions(screen.getByLabelText("Status"), "failed");

		await waitFor(() =>
			expect(
				page().queryByRole("link", { name: "Market assessment, Mopti" }),
			).not.toBeInTheDocument(),
		);
		expect(router.state.location.search).toMatchObject({ status: ["failed"] });
		expect(
			page().getByRole("link", { name: "Cash vs vouchers, Gao" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Remove filter: Failed" }),
		).toBeInTheDocument();
	});

	it("opens a shared link with its filters already applied", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao").tags = ["Mali"];
		api.addProject("Market assessment, Mopti");
		renderApp("/projects?group=none&tag=%5B%22Mali%22%5D");

		expect(
			await within(await screen.findByRole("main")).findByRole("link", {
				name: "Cash vs vouchers, Gao",
			}),
		).toBeInTheDocument();
		expect(
			page().queryByRole("link", { name: "Market assessment, Mopti" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Remove filter: Tag: Mali" }),
		).toBeInTheDocument();
	});

	it("sorts as chosen", async () => {
		const api = signedIn();
		api.addProject("Market assessment, Mopti");
		api.addProject("Biometric registration");
		const { user } = renderApp("/projects?group=none");
		await within(await screen.findByRole("main")).findByRole("link", {
			name: "Biometric registration",
		});

		await user.selectOptions(screen.getByLabelText("Sort"), "name");

		await waitFor(() =>
			expect(
				page()
					.getAllByRole("link", { name: /registration|assessment/ })
					.map((link) => link.textContent),
			).toEqual(["Biometric registration", "Market assessment, Mopti"]),
		);
	});

	it("shows archived projects only when asked", async () => {
		const api = signedIn();
		const archived = api.addProject("Shelter kits, Pemba");
		archived.archived_at = "2026-10-01T00:00:00Z";
		const { user } = renderApp(LIST);
		await screen.findByText("You have no projects yet.");

		await user.click(
			screen.getByRole("checkbox", { name: "Show archived projects" }),
		);

		const name = await screen.findByRole("rowheader", {
			name: /Shelter kits, Pemba/,
		});
		expect(name.closest("tr")).toHaveTextContent("Archived");
	});

	it("shows each project's status as the API derives it (§11.1)", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao").status = "brief_ready";
		api.addProject("Market assessment, Mopti").status = "failed";
		renderApp(LIST);

		const table = await screen.findByRole("table", { name: "Your projects" });
		const gao = within(table).getByRole("rowheader", {
			name: /Cash vs vouchers, Gao/,
		});
		const mopti = within(table).getByRole("rowheader", {
			name: /Market assessment, Mopti/,
		});
		expect(gao.closest("tr")).toHaveTextContent("Brief ready");
		expect(mopti.closest("tr")).toHaveTextContent("Failed");
	});

	it("offers selection only on the person's own projects", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao", "owner");
		api.addProject("Market assessment, Mopti", "viewer");
		renderApp(LIST);
		await screen.findByRole("table", { name: "Your projects" });

		expect(
			screen.getByRole("checkbox", { name: "Select Cash vs vouchers, Gao" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("checkbox", {
				name: "Select Market assessment, Mopti",
			}),
		).not.toBeInTheDocument();
	});

	it("archives the selected projects and says what happened", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao");
		const mopti = api.addProject("Market assessment, Mopti");
		const { user } = renderApp(LIST);

		await user.click(
			await screen.findByRole("checkbox", {
				name: "Select Cash vs vouchers, Gao",
			}),
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Select Market assessment, Mopti" }),
		);
		await user.click(screen.getByRole("button", { name: "Archive" }));

		expect(await screen.findByText("Archived 2 projects.")).toBeInTheDocument();
		expect(api.requestsTo("POST", "/project-actions")[0]?.body).toEqual({
			action: "archive",
			project_ids: [gao.id, mopti.id],
		});
	});

	it("says which projects a bulk action skipped", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const mopti = api.addProject("Market assessment, Mopti");
		const { user } = renderApp(LIST);
		await user.click(
			await screen.findByRole("checkbox", {
				name: "Select Cash vs vouchers, Gao",
			}),
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Select Market assessment, Mopti" }),
		);
		// Someone made Amara a viewer of Mopti after the list was drawn.
		mopti.role = "viewer";

		await user.click(screen.getByRole("button", { name: "Archive" }));

		expect(
			await screen.findByText(
				"Archived 1 project. 1 project was skipped: only their owners can change them.",
			),
		).toBeInTheDocument();
	});

	it("moves the selected projects to a collection", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp(LIST);

		await user.click(
			await screen.findByRole("checkbox", {
				name: "Select Cash vs vouchers, Gao",
			}),
		);
		await user.click(
			screen.getByRole("button", { name: "Move to collection…" }),
		);
		await user.click(await screen.findByRole("radio", { name: "Sahel 2026" }));
		await user.click(screen.getByRole("button", { name: "Move" }));

		expect(await screen.findByText("Moved 1 project.")).toBeInTheDocument();
		expect(gao.collection_id).toBe(sahel.id);
	});

	it("tags the selected projects, keeping their tags", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao");
		gao.tags = ["cash"];
		const { user } = renderApp(LIST);

		await user.click(
			await screen.findByRole("checkbox", {
				name: "Select Cash vs vouchers, Gao",
			}),
		);
		await user.click(screen.getByRole("button", { name: "Add a tag…" }));
		await user.type(await screen.findByLabelText("Tag"), "Mali");
		await user.click(screen.getByRole("button", { name: "Add tag" }));

		expect(await screen.findByText("Tagged 1 project.")).toBeInTheDocument();
		expect(gao.tags).toEqual(["cash", "Mali"]);
	});

	it("opens one collection's link as the filtered list", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		api.addProject("Market assessment, Mopti");
		const { router } = renderApp(`/c/${sahel.id}`);

		expect(
			await within(await screen.findByRole("main")).findByRole("link", {
				name: "Cash vs vouchers, Gao",
			}),
		).toBeInTheDocument();
		expect(currentPath(router)).toBe("/projects");
		expect(
			page().queryByRole("link", { name: "Market assessment, Mopti" }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations, as cards", async () => {
		const api = signedIn();
		api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").tags = ["Mali"];
		const { container } = renderApp("/projects");
		await within(await screen.findByRole("main")).findByRole("link", {
			name: "Cash vs vouchers, Gao",
		});

		await expectNoAccessibilityViolations(container);
	});

	it("has no accessibility violations, as a list", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { container } = renderApp("/projects?view=list");
		await screen.findByRole("table", { name: "Not in a collection" });

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
