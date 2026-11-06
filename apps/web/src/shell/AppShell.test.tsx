import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../test/axe";
import { currentPath, renderApp } from "../test/render";
import { fakeApi } from "../test/server";

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true, displayName: "Amara N." }));
	return api;
}

describe("the app shell (UI_UX_SPEC §4)", () => {
	it("has the rail as labelled navigation and the page as the main landmark", async () => {
		signedIn();
		renderApp("/");

		const rail = await screen.findByRole("navigation", { name: "Main" });
		expect(within(rail).getByRole("link", { name: "Home" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		expect(
			within(rail).getByRole("link", { name: "All projects" }),
		).toBeInTheDocument();
		expect(
			within(rail).getByRole("link", { name: "New project" }),
		).toBeInTheDocument();
		expect(
			within(rail).getByRole("link", { name: /Amara N\./ }),
		).toBeInTheDocument();
		expect(screen.getByRole("main")).toBeInTheDocument();
	});

	it("names the product Humani-T in the logo link and the tab title", async () => {
		signedIn();
		renderApp("/projects");

		expect(
			await screen.findByRole("link", { name: "Humani-T, home" }),
		).toBeInTheDocument();
		await waitFor(() => expect(document.title).toMatch(/ · Humani-T$/));
	});

	it("marks each recent project with a badge of its initials, which stays when the rail folds", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		renderApp("/");

		const rail = await screen.findByRole("navigation", { name: "Main" });
		const link = await within(rail).findByRole("link", {
			name: "Cash vs vouchers, Gao",
		});

		expect(within(link).getByText("CV")).toHaveAttribute("data-project-badge");
	});

	it("starts with a skip link to the main content", async () => {
		signedIn();
		const { user } = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });

		await user.tab();

		expect(
			screen.getByRole("link", { name: "Skip to main content" }),
		).toHaveFocus();
	});

	it("lists the person's recent projects in the rail", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		renderApp("/");

		const rail = await screen.findByRole("navigation", { name: "Main" });

		expect(
			await within(rail).findByRole("link", { name: "Cash vs vouchers, Gao" }),
		).toBeInTheDocument();
	});

	it("folds the rail with [ and remembers it", async () => {
		signedIn();
		const { user } = renderApp("/");
		const fold = await screen.findByRole("button", {
			name: "Fold the sidebar",
		});

		await user.keyboard("[[");

		expect(fold).toHaveAttribute("aria-expanded", "false");
		expect(window.localStorage.getItem("humani-t:rail-folded")).toBe("true");
	});

	it("ignores single-key shortcuts while the person types", async () => {
		signedIn();
		const { user } = renderApp("/projects/new");
		const name = await screen.findByLabelText("Project name");

		await user.type(name, "Gao [[draft]");

		expect(name).toHaveValue("Gao [draft]");
		expect(
			screen.getByRole("button", { name: "Fold the sidebar" }),
		).toHaveAttribute("aria-expanded", "true");
	});

	it("goes to projects with g then p", async () => {
		signedIn();
		const { user, router } = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });

		await user.keyboard("gp");

		await waitFor(() => expect(currentPath(router)).toBe("/projects"));
	});

	it("opens the rail as a drawer from the menu button on small screens", async () => {
		signedIn();
		const { user } = renderApp("/");

		await user.click(
			await screen.findByRole("button", { name: "Open the menu" }),
		);

		expect(screen.getByRole("navigation", { name: "Main" })).toHaveClass(
			"is-open",
		);
		await user.keyboard("{Escape}");
		expect(screen.getByRole("navigation", { name: "Main" })).not.toHaveClass(
			"is-open",
		);
	});

	it("signs out and returns to the sign-in page", async () => {
		const api = signedIn();
		const { user, router } = renderApp("/");

		await user.click(await screen.findByRole("button", { name: "Sign out" }));

		await waitFor(() => expect(currentPath(router)).toBe("/sign-in"));
		expect(api.session).toBeNull();
	});

	it("says so when the session has ended, without losing the page", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { user } = renderApp("/projects");
		await within(await screen.findByRole("main")).findByRole("link", {
			name: "Cash vs vouchers, Gao",
		});

		api.expireSession();
		await user.click(
			screen.getByRole("checkbox", { name: "Show archived projects" }),
		);

		const banner = await screen.findByRole("alert");
		expect(banner).toHaveTextContent(
			"You've been signed out. Sign in again to continue.",
		);
		expect(
			within(banner).getByRole("link", { name: "Sign in again" }),
		).toHaveAttribute("href", expect.stringContaining("/sign-in"));
		expect(
			screen.getByRole("heading", { level: 1, name: "All projects" }),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		signedIn();
		const { container } = renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Home" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("themes (UI_UX_SPEC §9.2)", () => {
	it("follows the system until the person chooses, then remembers the choice", async () => {
		signedIn();
		const { user } = renderApp("/");
		const theme = await screen.findByRole("button", {
			name: "Theme: System. Change theme",
		});

		expect(document.documentElement).not.toHaveAttribute("data-theme");
		await user.click(theme);

		expect(document.documentElement).toHaveAttribute("data-theme", "light");
		expect(window.localStorage.getItem("humani-t:theme")).toBe("light");
		await user.click(
			screen.getByRole("button", { name: "Theme: Light. Change theme" }),
		);
		expect(document.documentElement).toHaveAttribute("data-theme", "dark");
	});
});
