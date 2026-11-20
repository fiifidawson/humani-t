import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../test/axe";
import { currentPath, renderApp } from "../test/render";
import { fakeApi } from "../test/server";
import { setScreenWidth } from "../test/viewport";

beforeEach(() => setScreenWidth(1280));

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return api;
}

/** A review with a draft search plan: the chat has cards, the draft has a canvas. */
function planned() {
	const api = signedIn();
	const project = api.addProject("Cash vs vouchers, Gao");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	return { api, project, review, path: `/p/${project.id}/r/${review.id}` };
}

function briefed() {
	const fixture = planned();
	fixture.api.reviews.accept(fixture.review);
	const run = fixture.api.reviews.addRun(fixture.review, "done");
	fixture.api.reviews.addBrief(fixture.review, run, {});
	return fixture;
}

function turnSingleKeysOff() {
	window.localStorage.setItem("humani-t:single-key-shortcuts", "off");
}

describe("? shows the shortcuts (UI_UX_SPEC §11.5)", () => {
	it("lists every shortcut in a dialog that Esc closes", async () => {
		signedIn();
		const { user } = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });

		await user.keyboard("?");

		const dialog = await screen.findByRole("dialog", {
			name: "Keyboard shortcuts",
		});
		const rows = within(dialog).getAllByRole("row");
		expect(rows.map((row) => row.textContent)).toEqual(
			expect.arrayContaining([
				expect.stringContaining("Search and commands"),
				expect.stringContaining("Open or close the canvas"),
				expect.stringContaining("Next or previous card in the chat"),
				expect.stringContaining("Show shortcuts"),
			]),
		);
		await expectNoAccessibilityViolations(dialog);

		await user.keyboard("{Escape}");
		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Keyboard shortcuts" }),
			).not.toBeInTheDocument(),
		);
	});

	it("is not shown while the person types a question mark", async () => {
		signedIn();
		const { user } = renderApp("/projects/new");
		const name = await screen.findByLabelText("Project name");

		await user.type(name, "Cash?");

		expect(name).toHaveValue("Cash?");
		expect(
			screen.queryByRole("dialog", { name: "Keyboard shortcuts" }),
		).not.toBeInTheDocument();
	});
});

describe("turning single-key shortcuts off (WCAG 2.1.4)", () => {
	it("stops [ and ? from doing anything", async () => {
		signedIn();
		turnSingleKeysOff();
		const { user } = renderApp("/");
		const fold = await screen.findByRole("button", {
			name: "Fold the sidebar",
		});

		await user.keyboard("[[?");

		expect(fold).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.queryByRole("dialog", { name: "Keyboard shortcuts" }),
		).not.toBeInTheDocument();
	});

	it("keeps Ctrl K, which is not a single key", async () => {
		signedIn();
		turnSingleKeysOff();
		const { user } = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });

		await user.keyboard("{Control>}k{/Control}");

		expect(
			await screen.findByRole("dialog", { name: "Search and commands" }),
		).toBeInTheDocument();
	});

	it("stops ] from opening the canvas", async () => {
		const { path } = planned();
		turnSingleKeysOff();
		const { user, router } = renderApp(path);
		await screen.findByRole("log", { name: "Project conversation" });

		await user.keyboard("]");

		expect(currentPath(router)).toBe(path);
	});
});

describe("] opens or closes the canvas", () => {
	it("closes the open canvas, and opens the same one again", async () => {
		const { path } = planned();
		const { user, router } = renderApp(`${path}/draft`);
		await screen.findByRole("region", { name: /Search draft/ });

		await user.keyboard("]");
		await waitFor(() => expect(currentPath(router)).toBe(path));

		await user.keyboard("]");
		await waitFor(() => expect(currentPath(router)).toBe(`${path}/draft`));
	});

	it("opens the brief when there is one and no canvas was open yet", async () => {
		const { path } = briefed();
		const { user, router } = renderApp(path);
		await screen.findByRole("log", { name: "Project conversation" });
		await screen.findByRole("group", { name: "What the box does" });

		await user.keyboard("]");

		await waitFor(() => expect(currentPath(router)).toBe(`${path}/brief`));
	});

	it("opens the search draft before there is a run", async () => {
		const { path } = planned();
		const { user, router } = renderApp(path);
		await screen.findByRole("article", { name: /Search planner · Draft/ });

		await user.keyboard("]");

		await waitFor(() => expect(currentPath(router)).toBe(`${path}/draft`));
	});
});

describe("j and k move between chat cards", () => {
	it("moves to the next and previous card while the chat has focus", async () => {
		const { path } = planned();
		const { user } = renderApp(path);
		const log = await screen.findByRole("log", {
			name: "Project conversation",
		});
		await within(log).findByRole("article", { name: /Search planner · Draft/ });
		const cards = within(log).getAllByRole("article");
		expect(cards.length).toBeGreaterThan(1);

		log.focus();
		await user.keyboard("j");
		expect(cards[0]).toHaveFocus();

		await user.keyboard("j");
		expect(cards[1]).toHaveFocus();

		await user.keyboard("k");
		expect(cards[0]).toHaveFocus();
	});

	it("does nothing outside the chat", async () => {
		const { path } = planned();
		const { user } = renderApp(path);
		const log = await screen.findByRole("log", {
			name: "Project conversation",
		});
		await within(log).findByRole("article", { name: /Search planner · Draft/ });

		await user.keyboard("j");

		expect(within(log).getAllByRole("article")[0]).not.toHaveFocus();
	});

	it("opens a card's main action with Enter", async () => {
		const { path } = planned();
		const { user, router } = renderApp(path);
		const log = await screen.findByRole("log", {
			name: "Project conversation",
		});
		const draft = await within(log).findByRole("article", {
			name: /Search planner · Draft/,
		});

		draft.focus();
		await user.keyboard("{Enter}");

		await waitFor(() => expect(currentPath(router)).toBe(`${path}/draft`));
	});
});

describe("/ focuses the composer", () => {
	it("puts the cursor in the composer from anywhere on the page", async () => {
		const { path } = planned();
		const { user } = renderApp(path);
		const composer = await screen.findByRole("textbox", {
			name: /Changing the search/,
		});

		await user.keyboard("/");

		expect(composer).toHaveFocus();
		expect(composer).toHaveValue("");
	});
});

describe("Esc closes the source panel, then the canvas, and a dialog only itself", () => {
	it("leaves the source panel open when Esc closes the search", async () => {
		const { path } = briefed();
		const { user, router } = renderApp(`${path}/brief?source=P1`);
		await screen.findByRole("complementary", { name: "P1" });

		await user.keyboard("{Control>}k{/Control}");
		await screen.findByRole("dialog", { name: "Search and commands" });
		await user.keyboard("{Escape}");

		await waitFor(() =>
			expect(
				screen.queryByRole("dialog", { name: "Search and commands" }),
			).not.toBeInTheDocument(),
		);
		expect(
			screen.getByRole("complementary", { name: "P1" }),
		).toBeInTheDocument();

		await user.keyboard("{Escape}");
		await waitFor(() =>
			expect(
				screen.queryByRole("complementary", { name: "P1" }),
			).not.toBeInTheDocument(),
		);
		expect(
			screen.getByRole("region", { name: "Decision brief" }),
		).toBeInTheDocument();

		await user.keyboard("{Escape}");
		await waitFor(() => expect(currentPath(router)).toBe(path));
	});
});
