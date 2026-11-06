import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi, server } from "../../test/server";

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return api;
}

describe("Home (UI_UX_SPEC §7.1)", () => {
	it("shows recent projects, newest first, each as one link to its workspace", async () => {
		const api = signedIn();
		api.addProject(
			"Market assessment, Mopti",
			"reviewer",
			"2026-10-20T09:00:00Z",
		);
		const gao = api.addProject(
			"Cash vs vouchers, Gao",
			"owner",
			"2026-11-02T09:00:00Z",
		);
		renderApp("/");

		const recent = await screen.findByRole("region", {
			name: "Recent projects",
		});
		const links = await within(recent).findAllByRole("link");

		expect(links.map((link) => link.textContent)).toEqual([
			expect.stringContaining("Cash vs vouchers, Gao"),
			expect.stringContaining("Market assessment, Mopti"),
		]);
		expect(links[0]).toHaveAttribute("href", `/p/${gao.id}`);
		expect(links[1]).toHaveTextContent("Reviewer");
	});

	it("explains how to start when there are no projects yet", async () => {
		signedIn();
		renderApp("/");

		expect(
			await screen.findByRole("heading", { name: "Start with a question" }),
		).toBeInTheDocument();
		expect(
			within(screen.getByRole("main")).getByRole("link", {
				name: "New project",
			}),
		).toHaveAttribute("href", "/projects/new");
	});

	it("shows the API's message and a retry when the list cannot load", async () => {
		signedIn();
		server.use(
			http.get(
				"*/api/v1/projects",
				() =>
					HttpResponse.json(
						{
							type: "about:blank",
							title: "Service Unavailable",
							status: 503,
							detail: "The service is busy. Try again shortly.",
						},
						{
							status: 503,
							headers: { "content-type": "application/problem+json" },
						},
					),
				{ once: true },
			),
		);
		const { user } = renderApp("/");

		expect(
			await screen.findByText("The service is busy. Try again shortly."),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Try again" }));
		expect(
			await screen.findByRole("heading", { name: "Start with a question" }),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const api = signedIn();
		api.addProject("Cash vs vouchers, Gao");
		const { container } = renderApp("/");
		await screen.findByRole("region", { name: "Recent projects" });

		await expectNoAccessibilityViolations(container);
	});
});
