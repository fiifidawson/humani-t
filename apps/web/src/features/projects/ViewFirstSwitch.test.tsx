import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const LABEL =
	"Ask each member which option they lean towards before showing the brief";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	return { api, project, path: `/p/${project.id}/settings` };
}

async function openBriefs(path: string) {
	const view = renderApp(path);
	const section = await screen.findByRole("region", { name: "Briefs" });
	return { ...view, section };
}

describe("'Your view first' as the project's choice (UI_UX_SPEC §6.9, §16 Q3)", () => {
	it("is off for a new project", async () => {
		const { path } = withProject();
		const { section } = await openBriefs(path);

		const toggle = within(section).getByRole("checkbox", { name: LABEL });
		expect(toggle).not.toBeChecked();
		expect(toggle).toBeEnabled();
	});

	it("lets an owner turn it on, and says what that means", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openBriefs(path);

		await user.click(within(section).getByRole("checkbox", { name: LABEL }));

		await waitFor(() =>
			expect(
				within(section).getByRole("checkbox", { name: LABEL }),
			).toBeChecked(),
		);
		expect(
			api
				.requestsTo("PUT", `/projects/${project.id}/view-first`)
				.map((r) => r.body),
		).toEqual([{ is_view_first_on: true }]);
		expect(section).toHaveTextContent("Only they see their answer");
	});

	it("shows other members the setting without a switch", async () => {
		const { project, path } = withProject("reviewer");
		project.is_view_first_on = true;
		const { section } = await openBriefs(path);

		expect(within(section).queryByRole("checkbox")).not.toBeInTheDocument();
		expect(section).toHaveTextContent(
			"Each member is asked which option they lean towards before the brief.",
		);
		expect(section).toHaveTextContent("Only owners can change this.");
	});

	it("has no accessibility violations", async () => {
		const { path } = withProject();
		const { container } = await openBriefs(path);

		await expectNoAccessibilityViolations(container);
	});
});
