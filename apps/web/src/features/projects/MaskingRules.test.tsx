import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { DUPLICATE_RULE, NOT_YOUR_ROLE } from "../../test/fakeDocuments";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	return { api, project, path: `/p/${project.id}/settings` };
}

async function openPrivacy(path: string) {
	const view = renderApp(path);
	const section = await screen.findByRole("region", { name: "Privacy" });
	return { ...view, section };
}

describe("project masking rules in settings (UI_UX_SPEC §6.14)", () => {
	it("lists the project's rules for an owner, oldest first", async () => {
		const { api, project, path } = withProject();
		api.documents.addRule(project.id, "Kofi Mensah");
		api.documents.addRule(project.id, "Gao Clinic", "LOCATION");
		const { section } = await openPrivacy(path);

		const rules = await within(section).findByRole("list", {
			name: "Masking rules",
		});
		expect(
			within(rules)
				.getAllByRole("listitem")
				.map((item) => item.textContent),
		).toEqual([
			expect.stringContaining("Kofi Mensah · Person"),
			expect.stringContaining("Gao Clinic · Location"),
		]);
	});

	it("says so when the project has no rules yet", async () => {
		const { path } = withProject();
		const { section } = await openPrivacy(path);

		expect(
			await within(section).findByText("No masking rules yet."),
		).toBeInTheDocument();
	});

	it("adds a rule with the term and the type chosen", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openPrivacy(path);

		await user.type(
			await within(section).findByRole("textbox", { name: "Term" }),
			"Gao Clinic",
		);
		await user.selectOptions(
			within(section).getByRole("combobox", { name: "Mask as" }),
			"Location",
		);
		await user.click(within(section).getByRole("button", { name: "Add rule" }));

		expect(
			await within(section).findByText(/Gao Clinic · Location/),
		).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/projects/${project.id}/masking-rules`)[0]?.body,
		).toEqual({ term: "Gao Clinic", entity_type: "LOCATION" });
		expect(within(section).getByRole("textbox", { name: "Term" })).toHaveValue(
			"",
		);
	});

	it("shows the API's refusal of a term at the field", async () => {
		const { api, project, path } = withProject();
		api.documents.addRule(project.id, "Kofi Mensah");
		const { user, section } = await openPrivacy(path);

		await user.type(
			await within(section).findByRole("textbox", { name: "Term" }),
			"kofi mensah",
		);
		await user.click(within(section).getByRole("button", { name: "Add rule" }));

		expect(
			await within(section).findByRole("textbox", { name: "Term" }),
		).toHaveAccessibleDescription(expect.stringContaining(DUPLICATE_RULE));
	});

	it("removes a rule", async () => {
		const { api, project, path } = withProject();
		const rule = api.documents.addRule(project.id, "Kofi Mensah");
		const { user, section } = await openPrivacy(path);

		await user.click(
			await within(section).findByRole("button", {
				name: "Remove Kofi Mensah",
			}),
		);

		await waitFor(() =>
			expect(section).not.toHaveTextContent("Kofi Mensah · Person"),
		);
		expect(
			api.requestsTo(
				"DELETE",
				`/projects/${project.id}/masking-rules/${rule.id}`,
			),
		).toHaveLength(1);
		expect(api.documents.rules).toEqual([]);
	});

	it("shows the API's refusal when the owner is no longer one", async () => {
		const { api, project, path } = withProject();
		api.documents.addRule(project.id, "Kofi Mensah");
		const { user, section } = await openPrivacy(path);
		const remove = await within(section).findByRole("button", {
			name: "Remove Kofi Mensah",
		});

		project.role = "reviewer";
		await user.click(remove);

		expect(await within(section).findByRole("alert")).toHaveTextContent(
			NOT_YOUR_ROLE,
		);
	});

	it.each(["reviewer", "viewer"] as const)(
		"tells a %s that only owners see the rules, and never asks for them",
		async (role) => {
			const { api, project, path } = withProject(role);
			api.documents.addRule(project.id, "Kofi Mensah");
			const { section } = await openPrivacy(path);

			expect(section).toHaveTextContent(
				"Only owners can see and change the masking rules, because they hold names.",
			);
			expect(section).not.toHaveTextContent("Kofi Mensah");
			expect(
				api.requestsTo("GET", `/projects/${project.id}/masking-rules`),
			).toEqual([]);
		},
	);

	it("has no accessibility violations", async () => {
		const { api, project, path } = withProject();
		api.documents.addRule(project.id, "Kofi Mensah");
		const { container, section } = await openPrivacy(path);
		await within(section).findByRole("button", { name: "Remove Kofi Mensah" });

		await expectNoAccessibilityViolations(container);
	});
});
