import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const SCREENING = "Count camps as displaced settings.";
const WRITING = "Short sentences, no jargon.";
const REFUSED =
	"screening holds a term this project always masks. Remove it or reword it.";

function withProject(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true, displayName: "Amina K." }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	return { api, project, path: `/p/${project.id}/settings` };
}

async function openSection(path: string) {
	const view = renderApp(path);
	const section = await screen.findByRole("region", {
		name: "Instructions for the model",
	});
	return { ...view, section };
}

describe("instructions for the model in settings (UI_UX_SPEC §6.14)", () => {
	it("offers an owner two empty boxes with a hint and an example", async () => {
		const { path } = withProject();
		const { section } = await openSection(path);

		const judging = await within(section).findByRole("textbox", {
			name: "How to judge papers",
		});
		const writing = within(section).getByRole("textbox", {
			name: "How to write",
		});
		expect(judging).toHaveValue("");
		expect(judging).toHaveAttribute(
			"placeholder",
			"Treat studies in refugee camps as displaced settings.",
		);
		expect(writing).toHaveAttribute(
			"placeholder",
			"Write for a field manager: short sentences, no jargon.",
		);
		expect(judging).toHaveAccessibleDescription(
			expect.stringContaining("screening"),
		);
		expect(section).toHaveTextContent("Not saved yet.");
	});

	it("counts the characters typed against the limit", async () => {
		const { path } = withProject();
		const { user, section } = await openSection(path);
		const judging = await within(section).findByRole("textbox", {
			name: "How to judge papers",
		});

		await user.type(judging, "Camps");

		expect(judging).toHaveAccessibleDescription(
			expect.stringContaining("5 of 2,000 characters"),
		);
	});

	it("saves both texts as a new version and says who saved it, once the API confirms", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openSection(path);

		await user.type(
			await within(section).findByRole("textbox", {
				name: "How to judge papers",
			}),
			SCREENING,
		);
		await user.type(
			within(section).getByRole("textbox", { name: "How to write" }),
			WRITING,
		);
		await user.click(
			within(section).getByRole("button", { name: "Save instructions" }),
		);

		expect(
			await within(section).findByText(/Version 1, saved by Amina K\. on/),
		).toBeInTheDocument();
		expect(
			api.requestsTo("PUT", `/projects/${project.id}/model-instructions`)[0]
				?.body,
		).toEqual({
			screening: SCREENING,
			writing: WRITING,
			based_on_version: null,
		});
	});

	it("shows the saved version and edits on top of it", async () => {
		const { api, project, path } = withProject();
		api.instructions.add(project.id, { screening: SCREENING, writing: "" });
		const { user, section } = await openSection(path);

		const judging = await within(section).findByRole("textbox", {
			name: "How to judge papers",
		});
		expect(judging).toHaveValue(SCREENING);
		expect(section).toHaveTextContent(/Version 1, saved by .+ on 13 Nov 2026/);

		await user.type(judging, " Also host communities.");
		await user.click(
			within(section).getByRole("button", { name: "Save instructions" }),
		);

		expect(
			await within(section).findByText(/Version 2, saved by/),
		).toBeInTheDocument();
		expect(
			api.requestsTo("PUT", `/projects/${project.id}/model-instructions`)[0]
				?.body,
		).toMatchObject({ based_on_version: 1 });
	});

	it("shows the API's refusal, and keeps what was typed", async () => {
		const { api, path } = withProject();
		api.instructions.refusal = REFUSED;
		const { user, section } = await openSection(path);
		const judging = await within(section).findByRole("textbox", {
			name: "How to judge papers",
		});

		await user.type(judging, "Ask Kofi first.");
		await user.click(
			within(section).getByRole("button", { name: "Save instructions" }),
		);

		expect(await within(section).findByRole("alert")).toHaveTextContent(
			REFUSED,
		);
		expect(judging).toHaveValue("Ask Kofi first.");
		expect(section).toHaveTextContent("Not saved yet.");
	});

	it("says so when someone else saved first", async () => {
		const { api, project, path } = withProject();
		const { user, section } = await openSection(path);
		await user.type(
			await within(section).findByRole("textbox", {
				name: "How to write",
			}),
			WRITING,
		);

		api.instructions.add(project.id, { writing: "Another owner's text." });
		await user.click(
			within(section).getByRole("button", { name: "Save instructions" }),
		);

		expect(await within(section).findByRole("alert")).toHaveTextContent(
			"changed while you were editing",
		);
	});

	it.each(["reviewer", "viewer"] as const)(
		"shows a %s the instructions read-only",
		async (role) => {
			const { api, project, path } = withProject(role);
			api.instructions.add(project.id, { screening: SCREENING, writing: "" });
			const { section } = await openSection(path);

			await waitFor(() => expect(section).toHaveTextContent(SCREENING));
			expect(section).toHaveTextContent(
				"Only owners can change the instructions.",
			);
			expect(section).toHaveTextContent("Version 1, saved by");
			expect(within(section).queryByRole("textbox")).not.toBeInTheDocument();
			expect(
				within(section).queryByRole("button", { name: "Save instructions" }),
			).not.toBeInTheDocument();
		},
	);

	it("has no accessibility violations", async () => {
		const { api, project, path } = withProject();
		api.instructions.add(project.id, { screening: SCREENING, writing: "" });
		const { container, section } = await openSection(path);
		await within(section).findByRole("textbox", {
			name: "How to judge papers",
		});

		await expectNoAccessibilityViolations(container);
	});
});
