import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const NAME = "Cash vs vouchers, Gao";
const POLICY =
	"This deployment deletes a project 365 days after its last activity, unless an owner keeps it.";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	const user = api.addUser({ hasTotp: true });
	api.signInAs(user);
	const project = api.addProject(NAME, role);
	api.retention.periodDays = 365;
	api.retention.setDate(project.id, "2027-10-24T09:00:00Z", 337);
	return { api, user, project, path: `/p/${project.id}/settings` };
}

describe("a project's retention date in Project settings (UI_UX_SPEC §6.14, §7.7)", () => {
	it("says nothing about retention when the deployment sets no period", async () => {
		const { api, project, path } = withProject();
		api.retention.periodDays = null;
		renderApp(path);

		await screen.findByRole("region", { name: "General" });
		await waitFor(() =>
			expect(
				api.requestsTo("GET", `/projects/${project.id}/retention`),
			).toHaveLength(1),
		);

		expect(screen.queryByRole("region", { name: "Retention" })).toBeNull();
	});

	it("tells every member the deployment's period and the project's date", async () => {
		const { path } = withProject("viewer");
		renderApp(path);

		const section = await screen.findByRole("region", { name: "Retention" });

		await within(section).findByText(POLICY);
		expect(section).toHaveTextContent(
			"This project will be deleted on 24 Oct 2027, in 337 days.",
		);
		expect(section).toHaveTextContent("Only owners can keep the project.");
		expect(
			within(section).queryByRole("button", { name: "Keep project" }),
		).toBeNull();
	});

	it("lets an owner keep the project, which restarts its clock", async () => {
		const { api, project, path } = withProject();
		const { user } = renderApp(path);

		const section = await screen.findByRole("region", { name: "Retention" });
		await user.click(
			await within(section).findByRole("button", { name: "Keep project" }),
		);

		await waitFor(() =>
			expect(section).toHaveTextContent(
				"This project will be deleted on 21 Nov 2027, in 365 days.",
			),
		);
		expect(section).toHaveTextContent("Kept on 21 Nov 2026.");
		expect(
			api.requestsTo("POST", `/projects/${project.id}/retention/keep`),
		).toHaveLength(1);
	});

	it("has no accessibility violations", async () => {
		const { path } = withProject();
		const { container } = renderApp(path);

		const section = await screen.findByRole("region", { name: "Retention" });
		await within(section).findByRole("button", { name: "Keep project" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("Deleted in N days unless kept, in Needs you (UI_UX_SPEC §7.7)", () => {
	it("shows the real number of days, with Keep as its one action", async () => {
		const { api, project } = withProject();
		api.history.addRetentionNotice(project.id, 14);
		renderApp("/");

		const section = await screen.findByRole("region", {
			name: "Needs you (1)",
		});

		const [item] = within(section).getAllByRole("listitem");
		expect(item).toHaveTextContent(`${NAME} Deleted in 14 days unless kept`);
		expect(
			within(section).getByRole("button", {
				name: `Keep: ${NAME}: Deleted in 14 days unless kept`,
			}),
		).toBeInTheDocument();
	});

	it("keeps the project from Home, and the notice leaves", async () => {
		const { api, project } = withProject();
		api.history.addRetentionNotice(project.id, 3);
		const { user } = renderApp("/");

		const section = await screen.findByRole("region", {
			name: "Needs you (1)",
		});
		await user.click(
			within(section).getByRole("button", {
				name: `Keep: ${NAME}: Deleted in 3 days unless kept`,
			}),
		);

		await waitFor(() =>
			expect(screen.queryByRole("region", { name: /Needs you/ })).toBeNull(),
		);
		expect(
			api.requestsTo("POST", `/projects/${project.id}/retention/keep`),
		).toHaveLength(1);
	});

	it("has no accessibility violations", async () => {
		const { api, project } = withProject();
		api.history.addRetentionNotice(project.id, 1);
		const { container } = renderApp("/");

		await screen.findByRole("region", { name: "Needs you (1)" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("the deployment's period on the Account page (UI_UX_SPEC §7.7)", () => {
	it("tells an administrator what the period is", async () => {
		const { user } = withProject();
		user.is_admin = true;
		renderApp("/settings");

		const section = await screen.findByRole("region", {
			name: "Data retention",
		});

		await within(section).findByText(POLICY);
	});

	it("tells an administrator when nothing is deleted for age", async () => {
		const { api, user } = withProject();
		api.retention.periodDays = null;
		user.is_admin = true;
		renderApp("/settings");

		const section = await screen.findByRole("region", {
			name: "Data retention",
		});

		await within(section).findByText(
			"This deployment never deletes projects for age.",
		);
	});

	it("shows nothing about it to someone who is not an administrator", async () => {
		const { api } = withProject();
		renderApp("/settings");

		await screen.findByRole("region", { name: "Appearance" });

		expect(screen.queryByRole("region", { name: "Data retention" })).toBeNull();
		expect(api.requestsTo("GET", "/retention")).toHaveLength(0);
	});
});
