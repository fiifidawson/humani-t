import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { PASSWORD } from "../../test/fakeApi";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const LABEL =
	"Mask names, places and identifiers before text reaches the model";

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

describe("masking as the project's choice (UI_UX_SPEC §6.14, ADR 0014)", () => {
	it("shows an owner the switch, on, with what that means", async () => {
		const { path } = withProject();
		const { section } = await openPrivacy(path);

		const toggle = await within(section).findByRole("checkbox", {
			name: LABEL,
		});
		expect(toggle).toBeChecked();
		expect(toggle).toBeEnabled();
		expect(section).toHaveTextContent("Masking is on");
	});

	it("asks before turning masking off, and turns it off once confirmed", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openPrivacy(path);

		await user.click(
			await within(section).findByRole("checkbox", { name: LABEL }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Turn masking off?",
		});
		expect(dialog).toHaveTextContent("reach the model as written");
		expect(api.requestsTo("PUT", `/projects/${project.id}/masking`)).toEqual(
			[],
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Turn masking off" }),
		);

		await waitFor(() =>
			expect(
				within(section).getByRole("checkbox", { name: LABEL }),
			).not.toBeChecked(),
		);
		expect(section).toHaveTextContent("Masking is off");
		expect(api.projects[0]?.is_masking_on).toBe(false);
	});

	it("leaves masking on when the owner cancels", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openPrivacy(path);

		await user.click(
			await within(section).findByRole("checkbox", { name: LABEL }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Turn masking off?",
		});
		await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

		expect(
			within(section).getByRole("checkbox", { name: LABEL }),
		).toBeChecked();
		expect(api.requestsTo("PUT", `/projects/${project.id}/masking`)).toEqual(
			[],
		);
	});

	it("asks for the password again when the sign-in is not recent", async () => {
		const { api, project, path } = withProject();
		api.makeSignInStale();
		const { user, section } = await openPrivacy(path);

		await user.click(
			await within(section).findByRole("checkbox", { name: LABEL }),
		);
		await user.click(
			within(
				await screen.findByRole("dialog", { name: "Turn masking off?" }),
			).getByRole("button", { name: "Turn masking off" }),
		);
		const reauth = await screen.findByRole("dialog", { name: "Sign in again" });
		expect(reauth).toHaveTextContent(
			"Turning masking off needs you to sign in again.",
		);
		await user.type(within(reauth).getByLabelText("Password"), PASSWORD);
		await user.click(within(reauth).getByRole("button", { name: "Confirm" }));

		await waitFor(() => expect(api.projects[0]?.is_masking_on).toBe(false));
		expect(
			api.requestsTo("PUT", `/projects/${project.id}/masking`),
		).toHaveLength(2);
	});

	it("turns masking on again without asking", async () => {
		const { api, project, path } = withProject();
		project.is_masking_on = false;
		const { user, section } = await openPrivacy(path);

		await user.click(
			await within(section).findByRole("checkbox", { name: LABEL }),
		);

		await waitFor(() =>
			expect(
				within(section).getByRole("checkbox", { name: LABEL }),
			).toBeChecked(),
		);
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(api.projects[0]?.is_masking_on).toBe(true);
	});

	it("says why masking stays on when the deployment requires it", async () => {
		const { project, path } = withProject();
		project.can_turn_masking_off = false;
		const { section } = await openPrivacy(path);

		const toggle = await within(section).findByRole("checkbox", {
			name: LABEL,
		});
		expect(toggle).toBeDisabled();
		expect(section).toHaveTextContent(
			"This deployment sends text to a hosted model, so masking stays on.",
		);
	});

	it("shows the state that applies when the switch is off but the deployment requires masking", async () => {
		const { project, path } = withProject("viewer");
		project.is_masking_on = false;
		project.can_turn_masking_off = false;
		const { section } = await openPrivacy(path);

		await waitFor(() => expect(section).toHaveTextContent("Masking is on"));
	});

	it("tells reviewers and viewers the state, without a switch", async () => {
		const { project, path } = withProject("reviewer");
		project.is_masking_on = false;
		const { section } = await openPrivacy(path);

		await waitFor(() => expect(section).toHaveTextContent("Masking is off"));
		expect(section).toHaveTextContent("Only owners can change this.");
		expect(
			within(section).queryByRole("checkbox", { name: LABEL }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path } = withProject();
		const { container, section } = await openPrivacy(path);
		await within(section).findByRole("checkbox", { name: LABEL });

		await expectNoAccessibilityViolations(container);
	});
});
