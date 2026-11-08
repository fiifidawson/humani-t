import { act, cleanup, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";
import { setScreenWidth } from "../../test/viewport";

function planned() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", "owner");
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	return { api, review, path: `/p/${project.id}/r/${review.id}` };
}

function briefed() {
	const { api, review, path } = planned();
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	api.reviews.addBrief(review, run, {});
	return { path };
}

beforeEach(() => setScreenWidth(1280));

const width = (handle: HTMLElement) =>
	Number(handle.getAttribute("aria-valuenow"));

async function chatHandle(path: string) {
	const view = renderApp(`${path}/draft`);
	const handle = await screen.findByRole("separator", {
		name: "Resize the conversation",
	});
	return { ...view, handle };
}

describe("resizing the chat beside a canvas", () => {
	it("offers a handle between the chat and an open canvas, and none without one", async () => {
		const { path } = planned();
		renderApp(path);
		await screen.findByRole("article", { name: "Search planner · Draft 1" });
		expect(
			screen.queryByRole("separator", { name: "Resize the conversation" }),
		).not.toBeInTheDocument();
		cleanup();

		const { handle, container } = await chatHandle(path);

		expect(handle).toHaveAttribute("aria-orientation", "vertical");
		expect(width(handle)).toBe(640);
		expect(handle).toHaveAttribute("aria-valuemin", "440");
		expect(handle).toHaveAttribute("aria-valuemax", "960");
		await expectNoAccessibilityViolations(container);
	});

	it("has no handle on a phone, where the canvas takes the whole screen", async () => {
		setScreenWidth(400);
		const { path } = planned();
		renderApp(`${path}/draft`);
		await screen.findByRole("region", {
			name: "Search draft · version 1 (not accepted yet)",
		});

		expect(screen.queryByRole("separator")).not.toBeInTheDocument();
	});

	it("widens and narrows with the arrow keys, and stops at its limits", async () => {
		const { path } = planned();
		const { user, handle } = await chatHandle(path);
		act(() => handle.focus());

		await user.keyboard("{ArrowRight}");
		expect(width(handle)).toBe(656);
		await user.keyboard("{End}{ArrowRight}");
		expect(width(handle)).toBe(960);
		await user.keyboard("{Home}{ArrowLeft}");
		expect(width(handle)).toBe(440);
	});

	it("follows the pointer when dragged, within its limits", async () => {
		const { path } = planned();
		const { user, handle } = await chatHandle(path);

		await user.pointer([
			{ keys: "[MouseLeft>]", target: handle, coords: { clientX: 500 } },
			{ coords: { clientX: 580 } },
			{ keys: "[/MouseLeft]" },
		]);
		expect(width(handle)).toBe(720);

		await user.pointer([
			{ keys: "[MouseLeft>]", target: handle, coords: { clientX: 560 } },
			{ coords: { clientX: 2000 } },
			{ keys: "[/MouseLeft]" },
		]);
		expect(width(handle)).toBe(960);
	});

	it("stays under the pointer when the page is zoomed", async () => {
		const { path } = planned();
		const { user, handle } = await chatHandle(path);
		// The signed-in app is drawn at 75%: 60 screen pixels are 80 of the page's.
		Object.defineProperty(handle, "currentCSSZoom", { value: 0.75 });

		await user.pointer([
			{ keys: "[MouseLeft>]", target: handle, coords: { clientX: 500 } },
			{ coords: { clientX: 560 } },
			{ keys: "[/MouseLeft]" },
		]);

		expect(width(handle)).toBe(720);
	});

	it("keeps the width for the next visit, and a double click puts it back", async () => {
		const { path } = planned();
		const first = await chatHandle(path);
		act(() => first.handle.focus());
		await first.user.keyboard("{End}");
		cleanup();

		const { user, handle } = await chatHandle(path);
		expect(width(handle)).toBe(960);

		await user.dblClick(handle);
		expect(width(handle)).toBe(640);
	});
});

describe("resizing the source panel", () => {
	it("widens towards the brief, within its limits", async () => {
		const { path } = briefed();
		const { user } = renderApp(`${path}/brief?source=P1`);
		const handle = await screen.findByRole("separator", {
			name: "Resize the source panel",
		});
		expect(width(handle)).toBe(400);
		act(() => handle.focus());

		await user.keyboard("{ArrowLeft}");
		expect(width(handle)).toBe(416);

		await user.pointer([
			{ keys: "[MouseLeft>]", target: handle, coords: { clientX: 600 } },
			{ coords: { clientX: 500 } },
			{ keys: "[/MouseLeft]" },
		]);
		expect(width(handle)).toBe(516);

		await user.keyboard("{End}");
		expect(width(handle)).toBe(640);
	});
});

describe("a panel at full width", () => {
	it("takes the whole workspace and gives the chat back", async () => {
		const { path } = planned();
		const { user } = renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", { name: /Search draft/ });
		const toggle = within(canvas).getByRole("button", { name: "Full width" });
		expect(toggle).toHaveAttribute("aria-pressed", "false");

		await user.click(toggle);

		expect(toggle).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.queryByRole("log", { name: "Project conversation" }),
		).not.toBeInTheDocument();
		expect(screen.queryByRole("separator")).not.toBeInTheDocument();

		await user.click(toggle);

		expect(
			screen.getByRole("log", { name: "Project conversation" }),
		).toBeInTheDocument();
	});

	it("gives the chat back on Esc before closing the panel on a second Esc", async () => {
		const { path } = planned();
		const { user } = renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", { name: /Search draft/ });
		await user.click(
			within(canvas).getByRole("button", { name: "Full width" }),
		);

		await user.keyboard("{Escape}");

		expect(
			screen.getByRole("region", { name: /Search draft/ }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("log", { name: "Project conversation" }),
		).toBeInTheDocument();

		await user.keyboard("{Escape}");

		await screen.findByRole("log", { name: "Project conversation" });
		expect(
			screen.queryByRole("region", { name: /Search draft/ }),
		).not.toBeInTheDocument();
	});

	it("is not offered on a phone, where the panel already fills the screen", async () => {
		setScreenWidth(400);
		const { path } = planned();
		renderApp(`${path}/draft`);
		const canvas = await screen.findByRole("region", { name: /Search draft/ });

		expect(
			within(canvas).queryByRole("button", { name: "Full width" }),
		).not.toBeInTheDocument();
	});
});
