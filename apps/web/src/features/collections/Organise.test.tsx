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

describe("Pinned on Home (UI_UX_SPEC §7.1)", () => {
	it("shows the person's pinned projects, newest pin first", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao").pinned_at = "2026-11-10T09:00:00Z";
		api.addProject("Biometric registration").pinned_at = "2026-11-12T09:00:00Z";
		api.addProject("Market assessment, Mopti");
		renderApp("/");

		const pinned = await screen.findByRole("region", { name: "Pinned" });

		expect(
			within(pinned)
				.getAllByRole("link")
				.map((link) => link.textContent),
		).toEqual(["Biometric registration", "Cash vs vouchers, Gao"]);
	});

	it("shows at most eight", async () => {
		const api = signedIn();
		for (let n = 1; n <= 9; n++) {
			api.addProject(`Project ${n}`).pinned_at = `2026-11-1${n % 10}T09:00:00Z`;
		}
		renderApp("/");

		const pinned = await screen.findByRole("region", { name: "Pinned" });

		expect(within(pinned).getAllByRole("link")).toHaveLength(8);
	});

	it("shows where each is filed, and its papers and people", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		const gao = api.addProject("Cash vs vouchers, Gao");
		Object.assign(gao, {
			pinned_at: "2026-11-10T09:00:00Z",
			collection_id: sahel.id,
			tags: ["Mali", "cash"],
			status: "brief_ready",
			brief_version: 2,
			relevant_papers: 52,
			papers_found: 340,
		});
		renderApp("/");

		const pinned = await screen.findByRole("region", { name: "Pinned" });

		expect(
			await within(pinned).findByText("Sahel 2026 · Mali · cash"),
		).toBeInTheDocument();
		expect(within(pinned).getByText("Brief v2")).toBeInTheDocument();
		expect(within(pinned).getByText("52 relevant of 340")).toBeInTheDocument();
		expect(within(pinned).getByText("1 member")).toBeInTheDocument();
	});

	it("pins a project from its card and unpins it again", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp("/projects");

		await user.click(
			await screen.findByRole("button", {
				name: "Pin Cash vs vouchers, Gao to Home",
			}),
		);

		await waitFor(() => expect(gao.pinned_at).not.toBeNull());
		await user.click(
			await screen.findByRole("button", {
				name: "Unpin Cash vs vouchers, Gao",
			}),
		);
		await waitFor(() => expect(gao.pinned_at).toBeNull());
	});

	it("shows nothing when nothing is pinned", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		renderApp("/");
		await screen.findByRole("region", { name: "Recent projects" });

		expect(
			screen.queryByRole("region", { name: "Pinned" }),
		).not.toBeInTheDocument();
	});
});

describe("Collections on Home (UI_UX_SPEC §7.1)", () => {
	it("lists collections holding the person's projects, with counts, then the rest", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026", false);
		api.collections.add("Protection");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		api.addProject("Market assessment, Mopti");
		renderApp("/");

		const row = await screen.findByRole("region", { name: "Collections" });

		expect(
			within(row)
				.getAllByRole("link")
				.map((link) => link.textContent),
		).toEqual(["Sahel 2026 (1)", "Not in a collection (1)"]);
	});

	it("opens the list filtered to a collection", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		const { user, router } = renderApp("/");

		await user.click(
			await within(
				await screen.findByRole("region", { name: "Collections" }),
			).findByRole("link", { name: "Sahel 2026 (1)" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/projects"));
		expect(router.state.location.search).toMatchObject({
			collection: sahel.id,
		});
	});

	it("has no accessibility violations with pins and collections", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		const gao = api.addProject("Cash vs vouchers, Gao");
		gao.collection_id = sahel.id;
		gao.pinned_at = "2026-11-10T09:00:00Z";
		const { container } = renderApp("/");
		await screen.findByRole("region", { name: "Collections" });
		await screen.findByRole("region", { name: "Pinned" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("collections in the rail (UI_UX_SPEC §7.3)", () => {
	it("shows each collection with its count, folded until opened", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		const { user } = renderApp("/settings");
		const rail = await screen.findByRole("navigation", { name: "Main" });

		const toggle = await within(rail).findByRole("button", {
			name: /^Sahel 2026.*1 project$/,
		});
		expect(toggle).toHaveAttribute("aria-expanded", "false");

		await user.click(toggle);

		expect(toggle).toHaveAttribute("aria-expanded", "true");
		expect(
			within(rail).getAllByRole("link", { name: "Cash vs vouchers, Gao" }),
		).toHaveLength(2);
	});

	it("remembers which collections were open", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026");
		api.addProject("Cash vs vouchers, Gao").collection_id = sahel.id;
		const first = renderApp("/settings");
		const rail = await screen.findByRole("navigation", { name: "Main" });
		await first.user.click(
			await within(rail).findByRole("button", { name: /^Sahel 2026/ }),
		);
		first.unmount();

		renderApp("/settings");

		expect(
			await within(
				await screen.findByRole("navigation", { name: "Main" }),
			).findByRole("button", { name: /^Sahel 2026/ }),
		).toHaveAttribute("aria-expanded", "true");
	});
});

describe("filing a project from its workspace (UI_UX_SPEC §7.3)", () => {
	it("moves the project with the ⋯ menu's Move to collection…", async () => {
		const api = signedIn();
		const sahel = api.collections.add("Sahel 2026", false);
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp(`/p/${gao.id}`);

		await user.click(
			await screen.findByRole("button", { name: "More project actions" }),
		);
		await user.click(
			await screen.findByRole("button", { name: "Move to collection…" }),
		);
		await user.click(await screen.findByRole("radio", { name: "Sahel 2026" }));
		await user.click(screen.getByRole("button", { name: "Move" }));

		await waitFor(() => expect(gao.collection_id).toBe(sahel.id));
	});

	it("creates a collection on the way, saying everyone can see its name", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp(`/p/${gao.id}/settings`);

		await user.click(
			await screen.findByRole("button", { name: "Move to collection…" }),
		);
		const name = await screen.findByLabelText("New collection");
		expect(name).toHaveAccessibleDescription(
			expect.stringContaining("Everyone in this deployment can see"),
		);
		await user.type(name, "Sahel 2026");
		await user.click(screen.getByRole("button", { name: "Create collection" }));
		expect(
			await screen.findByRole("radio", { name: "Sahel 2026" }),
		).toBeChecked();
		await user.click(screen.getByRole("button", { name: "Move" }));

		await waitFor(() =>
			expect(gao.collection_id).toBe(api.collections.collections[0]?.id),
		);
	});

	it("explains a name already in use", async () => {
		const api = signedIn();
		api.collections.add("Sahel 2026", false);
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp(`/p/${gao.id}/settings`);

		await user.click(
			await screen.findByRole("button", { name: "Move to collection…" }),
		);
		await user.type(
			await screen.findByLabelText("New collection"),
			"sahel 2026",
		);
		await user.click(screen.getByRole("button", { name: "Create collection" }));

		expect(
			await screen.findByText("A collection with this name already exists."),
		).toBeInTheDocument();
	});

	it("pins the project from the ⋯ menu", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao", "viewer");
		const { user } = renderApp(`/p/${gao.id}`);

		await user.click(
			await screen.findByRole("button", { name: "More project actions" }),
		);
		expect(
			screen.queryByRole("button", { name: "Move to collection…" }),
		).not.toBeInTheDocument();
		await user.click(
			await screen.findByRole("button", { name: "Pin to Home" }),
		);

		await waitFor(() => expect(gao.pinned_at).not.toBeNull());
	});
});

describe("tags in project settings (UI_UX_SPEC §6.14)", () => {
	it("lets an owner set the project's tags", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp(`/p/${gao.id}/settings`);

		await user.type(await screen.findByLabelText("Tags"), "Mali, cash, mali");
		await user.click(screen.getByRole("button", { name: "Save tags" }));

		await waitFor(() => expect(gao.tags).toEqual(["Mali", "cash"]));
		expect(await screen.findByLabelText("Tags")).toHaveValue("Mali, cash");
	});

	it("shows others the tags and who may change them", async () => {
		const api = signedIn();
		const gao = api.addProject("Cash vs vouchers, Gao", "reviewer");
		gao.tags = ["Mali"];
		renderApp(`/p/${gao.id}/settings`);

		expect(await screen.findByText("Tags: Mali")).toBeInTheDocument();
		expect(
			screen.getByText("Only owners can change the collection and tags."),
		).toBeInTheDocument();
		expect(screen.queryByLabelText("Tags")).not.toBeInTheDocument();
	});
});
