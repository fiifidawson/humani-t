import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

function withProject() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao");
	return { api, project };
}

describe("Needs you on Home (UI_UX_SPEC §7.1)", () => {
	it("lists each item with its project and one action to where it waits", async () => {
		const { api, project } = withProject();
		const failed = api.history.addNeedsYou(project.id, {
			kind: "failed_run",
			run_number: 3,
			stopped_at: "screen",
			at: ago(2 * HOUR),
		});
		const draft = api.history.addNeedsYou(project.id, {
			kind: "draft",
			version: 2,
			run_id: null,
		});
		renderApp("/");

		const section = await screen.findByRole("region", {
			name: "Needs you (2)",
		});
		const items = within(section).getAllByRole("listitem");
		expect(items[0]).toHaveTextContent(
			"Cash vs vouchers, Gao Run 3 stopped at Screen · 2 h ago",
		);
		expect(
			within(section).getByRole("link", {
				name: "Open: Cash vs vouchers, Gao: Run 3 stopped at Screen",
			}),
		).toHaveAttribute(
			"href",
			`/p/${project.id}/r/${failed.review_id}/run/${failed.run_id}`,
		);
		expect(
			within(section).getByRole("link", {
				name: "Open: Cash vs vouchers, Gao: Draft 2 waiting for your accept",
			}),
		).toHaveAttribute(
			"href",
			`/p/${project.id}/r/${draft.review_id}/draft?version=2`,
		);
	});

	it("names a gate in plain words, with Review as its action", async () => {
		const { api, project } = withProject();
		api.history.addNeedsYou(project.id, {
			kind: "gate",
			gate_kind: "privacy_review",
		});
		renderApp("/");

		const section = await screen.findByRole("region", {
			name: "Needs you (1)",
		});
		expect(section).toHaveTextContent("Privacy review · run 1 is waiting");
		expect(
			within(section).getByRole("link", { name: /^Review: / }),
		).toBeInTheDocument();
	});

	it("dismisses an item for good, and asks the API to", async () => {
		const { api, project } = withProject();
		const item = api.history.addNeedsYou(project.id, {
			kind: "unread_brief",
			brief_id: "b-1",
		});
		const { user } = renderApp("/");
		const section = await screen.findByRole("region", {
			name: "Needs you (1)",
		});

		await user.click(
			within(section).getByRole("button", { name: /^Dismiss: / }),
		);

		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: /Needs you/ }),
			).not.toBeInTheDocument(),
		);
		expect(
			api.requests.find(
				(r) => r.method === "POST" && r.path === "/api/v1/needs-you/dismissals",
			)?.body,
		).toEqual({ key: item.key });
	});

	it("shows its count in the rail", async () => {
		const { api, project } = withProject();
		api.history.addNeedsYou(project.id, { kind: "failed_run" });
		api.history.addNeedsYou(project.id, { kind: "failed_run" });
		renderApp("/");

		const rail = await screen.findByRole("navigation", { name: "Main" });
		await screen.findByRole("region", { name: "Needs you (2)" });
		const home = await within(rail).findByRole("link", { name: /need you/ });
		expect(home).toHaveAttribute("href", "/");
		expect(home).toHaveTextContent("Home, 2 need you");
	});

	it("is left out when nothing waits", async () => {
		withProject();
		renderApp("/");

		await screen.findByRole("region", { name: "Recent projects" });
		expect(
			screen.queryByRole("region", { name: /Needs you/ }),
		).not.toBeInTheDocument();
	});
});

describe("Recent activity on Home (UI_UX_SPEC §7.1)", () => {
	it("groups lines by day, newest first, each linking to what it is about", async () => {
		const { api, project } = withProject();
		const brief = api.history.event(project.id, {
			action: "brief_ready",
			kind: "brief",
			version: 2,
			run_id: "run-3",
			model_ids: ["qwen3.5:9b"],
			at: ago(0),
		});
		api.history.activity.push(
			brief,
			api.history.event(project.id, {
				action: "question_asked",
				kind: "question",
				actor: { id: "u-2", display_name: "Maya K." },
				text: "How fast do prices rise after a distribution?",
				thread_id: "t-1",
				at: ago(DAY),
			}),
			api.history.event(project.id, {
				action: "run",
				kind: "run",
				run_number: 1,
				run_state: "done",
				run_id: "run-1",
				at: ago(60 * DAY),
			}),
		);
		renderApp("/");

		const section = await screen.findByRole("region", {
			name: "Recent activity",
		});
		const days = (
			await within(section).findAllByRole("heading", { level: 3 })
		).map((h) => h.textContent);
		expect(days.slice(0, 2)).toEqual(["Today", "Yesterday"]);
		expect(days).toHaveLength(3);
		expect(
			within(section).getByRole("link", {
				name: "Brief v2 ready · qwen3.5:9b",
			}),
		).toHaveAttribute(
			"href",
			`/p/${project.id}/r/${brief.review_id}/brief?run=run-3`,
		);
		expect(section).toHaveTextContent(
			'Maya K. asked "How fast do prices rise after a distribution?" · Cash vs vouchers, Gao',
		);
		expect(
			within(section).getByRole("link", { name: "Run 1 finished" }),
		).toBeInTheDocument();
	});

	it("explains how to start instead, for someone with no projects", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		renderApp("/");

		expect(
			await screen.findByRole("heading", { name: "Start with a question" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("region", { name: "Recent activity" }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { api, project } = withProject();
		api.history.addNeedsYou(project.id, { kind: "failed_run" });
		api.history.activity.push(
			api.history.event(project.id, {
				action: "brief_ready",
				kind: "brief",
				version: 1,
				at: ago(0),
			}),
		);
		const { container } = renderApp("/");
		await screen.findByRole("region", { name: "Recent activity" });
		await screen.findByRole("region", { name: "Needs you (1)" });

		await expectNoAccessibilityViolations(container);
	});
});
