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

/** A project with a brief claim, a paper and a follow-up question that mention prices. */
function withResults() {
	const api = signedIn();
	const project = api.addProject("Cash vs vouchers, Gao");
	api.addProject("Market assessment, Mopti", "viewer", "2026-10-01T10:00:00Z");
	api.search.add(project.id, {
		scope: "briefs",
		text: "Prices can rise in small markets",
		review_id: "r-1",
		run_id: "run-3",
		brief_version: 2,
	});
	api.search.add(project.id, {
		scope: "papers",
		text: "Cash transfers and food prices in Mali",
		title: "Cash transfers and food prices in Mali",
		review_id: "r-1",
		run_id: "run-3",
		paper_id: "paper-12",
		tag: "P0012",
	});
	api.search.add(project.id, {
		scope: "questions",
		text: "How fast do prices rise after a distribution?",
		review_id: "r-1",
		thread_id: "t-1",
		question_id: "q-1",
	});
	return { api, project };
}

async function openPalette(path = "/") {
	const view = renderApp(path);
	await screen.findByRole("navigation", { name: "Main" });
	await view.user.keyboard("{Control>}k{/Control}");
	const dialog = await screen.findByRole("dialog", {
		name: "Search and commands",
	});
	const box = within(dialog).getByRole("combobox", {
		name: "Search or type a command",
	});
	await waitFor(() => expect(box).toHaveFocus());
	return { ...view, dialog, box };
}

describe("opening and closing (UI_UX_SPEC §7.6, §11.5)", () => {
	it("opens with Ctrl K from anywhere, even from a text field, with the box focused", async () => {
		signedIn();
		const { user } = renderApp("/projects/new");
		const name = await screen.findByLabelText("Project name");
		await user.click(name);

		await user.keyboard("{Control>}k{/Control}");

		const dialog = await screen.findByRole("dialog", {
			name: "Search and commands",
		});
		await waitFor(() =>
			expect(within(dialog).getByRole("combobox")).toHaveFocus(),
		);
		expect(name).toHaveValue("");
	});

	it("opens with ⌘K on a Mac", async () => {
		signedIn();
		const { user } = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });

		await user.keyboard("{Meta>}k{/Meta}");

		expect(
			await screen.findByRole("dialog", { name: "Search and commands" }),
		).toBeInTheDocument();
	});

	it("opens from the search button in the header", async () => {
		signedIn();
		const { user } = renderApp("/");

		await user.click(await screen.findByRole("button", { name: "Search" }));

		expect(
			await screen.findByRole("dialog", { name: "Search and commands" }),
		).toBeInTheDocument();
	});

	it("closes with Esc", async () => {
		signedIn();
		const { user } = await openPalette();

		await user.keyboard("{Escape}");

		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Search and commands" }),
			).not.toBeInTheDocument(),
		);
	});
});

describe("with an empty box", () => {
	it("shows recent projects, newest first, and the commands", async () => {
		const api = signedIn();
		api.addProject("Market assessment, Mopti", "owner", "2026-10-01T10:00:00Z");
		api.addProject("Cash vs vouchers, Gao", "owner", "2026-11-02T10:00:00Z");
		const { dialog } = await openPalette();

		const recent = await within(dialog).findByRole("group", {
			name: "Recent projects",
		});
		const options = within(recent).getAllByRole("option");
		expect(options.map((o) => o.textContent)).toEqual([
			expect.stringContaining("Cash vs vouchers, Gao"),
			expect.stringContaining("Market assessment, Mopti"),
		]);
		expect(
			within(dialog).getByRole("group", { name: "Commands" }),
		).toBeInTheDocument();
	});

	it("lists what needs you first, naming the project", async () => {
		const api = signedIn();
		const project = api.addProject("Cash vs vouchers, Gao");
		api.history.addNeedsYou(project.id, {
			kind: "failed_run",
			run_number: 3,
			stopped_at: "screen",
		});
		const { dialog } = await openPalette();

		const needsYou = await within(dialog).findByRole("group", {
			name: "Needs you",
		});
		expect(within(needsYou).getByRole("option")).toHaveTextContent(
			"Run 3 stopped at Screen · Cash vs vouchers, Gao",
		);
	});

	it("opens a recent project with Enter", async () => {
		const api = signedIn();
		const project = api.addProject("Cash vs vouchers, Gao");
		const { user, router, dialog } = await openPalette();
		await within(dialog).findByRole("option", { name: /Cash vs vouchers/ });

		await user.keyboard("{Enter}");

		await waitFor(() => expect(currentPath(router)).toBe(`/p/${project.id}`));
		expect(
			screen.queryByRole("dialog", { name: "Search and commands" }),
		).not.toBeInTheDocument();
	});
});

describe("searching", () => {
	it("groups results by project, saying what each one is", async () => {
		withResults();
		const { user, dialog, box } = await openPalette();

		await user.type(box, "prices");

		const group = await within(dialog).findByRole("group", {
			name: "Cash vs vouchers, Gao",
		});
		const options = within(group).getAllByRole("option");
		expect(options.map((o) => o.textContent)).toEqual([
			expect.stringMatching(/Brief v2.*Prices can rise in small markets/),
			expect.stringMatching(/P0012.*Cash transfers and food prices in Mali/),
			expect.stringMatching(/Question.*How fast do prices rise/),
		]);
		expect(
			within(dialog).queryByRole("group", { name: "Market assessment, Mopti" }),
		).not.toBeInTheDocument();
	});

	it("searches on the server, only once the box holds two characters", async () => {
		const { api } = withResults();
		const { user, dialog, box } = await openPalette();

		await user.type(box, "p");
		await within(dialog).findByRole("group", { name: "Commands" });
		expect(api.requestsTo("GET", "/search")).toHaveLength(0);

		await user.type(box, "r");
		await within(dialog).findByRole("group", { name: "Cash vs vouchers, Gao" });
		expect(api.requestsTo("GET", "/search").length).toBeGreaterThan(0);
	});

	it("changes the scope with Tab and back with Shift Tab", async () => {
		const { api } = withResults();
		const { user, dialog, box } = await openPalette();
		await user.type(box, "prices");
		await within(dialog).findByRole("group", { name: "Cash vs vouchers, Gao" });

		await user.keyboard("{Tab}");

		const scopes = within(dialog).getByRole("group", { name: "Search in" });
		expect(
			within(scopes).getByRole("button", { name: "Projects" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(box).toHaveFocus();
		await waitFor(() =>
			expect(
				within(dialog).queryByRole("option", { name: /Brief v2/ }),
			).not.toBeInTheDocument(),
		);
		await waitFor(() =>
			expect(api.search.requested.some((r) => r.scope === "projects")).toBe(
				true,
			),
		);

		await user.keyboard("{Shift>}{Tab}{/Shift}");

		expect(within(scopes).getByRole("button", { name: "All" })).toHaveAttribute(
			"aria-pressed",
			"true",
		);
	});

	it("chooses a scope with a click too", async () => {
		withResults();
		const { user, dialog, box } = await openPalette();
		await user.type(box, "prices");

		await user.click(within(dialog).getByRole("button", { name: "Papers" }));

		const group = await within(dialog).findByRole("group", {
			name: "Cash vs vouchers, Gao",
		});
		await waitFor(() =>
			expect(within(group).getAllByRole("option")).toHaveLength(1),
		);
		expect(box).toHaveFocus();
	});

	it("moves through the results with the arrow keys and opens one with Enter", async () => {
		const { project } = withResults();
		const { user, router, dialog, box } = await openPalette();
		await user.type(box, "prices");
		const brief = await within(dialog).findByRole("option", {
			name: /Brief v2/,
		});
		expect(box).toHaveAttribute("aria-activedescendant", brief.id);
		expect(brief).toHaveAttribute("aria-selected", "true");

		await user.keyboard("{ArrowDown}");
		const paper = within(dialog).getByRole("option", { name: /P0012/ });
		expect(box).toHaveAttribute("aria-activedescendant", paper.id);
		await user.keyboard("{ArrowUp}{Enter}");

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/r-1/brief`),
		);
		expect(router.state.location.search).toEqual({ run: "run-3" });
	});

	it("opens a follow-up question in its thread with a click", async () => {
		const { project } = withResults();
		const { user, router, dialog, box } = await openPalette();
		await user.type(box, "prices");

		await user.click(
			await within(dialog).findByRole("option", { name: /How fast/ }),
		);

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/ask/t-1`),
		);
	});

	it("says so when nothing matches", async () => {
		withResults();
		const { user, dialog, box } = await openPalette();

		await user.type(box, "earthquake");

		expect(
			await within(dialog).findByText("No results for “earthquake”."),
		).toBeInTheDocument();
	});

	it("says when there may be more than it shows", async () => {
		const { api } = withResults();
		api.search.isTruncated = true;
		const { user, dialog, box } = await openPalette();

		await user.type(box, "prices");

		expect(
			await within(dialog).findByText(
				"Showing the first results only. Add words to narrow the search.",
			),
		).toBeInTheDocument();
	});

	it("shows the API's message when searches are refused for a while", async () => {
		const { api } = withResults();
		api.search.throttleNext = 30;
		const { user, dialog, box } = await openPalette();

		await user.type(box, "prices");

		expect(
			await within(dialog).findByText(/Too many attempts/),
		).toBeInTheDocument();
	});
});

describe("commands", () => {
	it("toggles dark mode", async () => {
		signedIn();
		const { user, dialog, box } = await openPalette();

		await user.type(box, "dark");
		await within(dialog).findByRole("option", { name: "Toggle dark mode" });
		await user.keyboard("{Enter}");

		expect(document.documentElement).toHaveAttribute("data-theme", "dark");
		expect(document.documentElement).toHaveClass("dark");
	});

	it("goes to settings", async () => {
		signedIn();
		const { user, router, dialog, box } = await openPalette();

		await user.type(box, "settings");
		await user.click(
			await within(dialog).findByRole("option", { name: "Go to settings" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/settings"));
	});

	it("starts a new project", async () => {
		signedIn();
		const { user, router, dialog, box } = await openPalette();

		await user.type(box, "new pro");
		await user.click(
			await within(dialog).findByRole("option", { name: "New project" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe("/projects/new"));
	});
});

describe("accessibility", () => {
	it("has no violations with an empty box", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { dialog } = await openPalette();
		await within(dialog).findByRole("group", { name: "Recent projects" });

		await expectNoAccessibilityViolations(dialog);
	});

	it("has no violations with results", async () => {
		withResults();
		const { user, dialog, box } = await openPalette();
		await user.type(box, "prices");
		await within(dialog).findByRole("group", { name: "Cash vs vouchers, Gao" });

		await expectNoAccessibilityViolations(dialog);
	});
});
