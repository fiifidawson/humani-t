import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function signedInWith(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return { api, project: api.addProject("Cash vs vouchers, Gao", role) };
}

describe("the project workspace, empty (UI_UX_SPEC §4.1, §5.4)", () => {
	it("shows the project's name and the conversation as a log", async () => {
		const { project } = signedInWith("owner");
		renderApp(`/p/${project.id}`);

		expect(
			await screen.findByRole("heading", {
				level: 1,
				name: "Cash vs vouchers, Gao",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("log", { name: "Project conversation" }),
		).toBeInTheDocument();
		expect(
			screen.getByText("What decision do you need evidence for?", {
				selector: "p",
			}),
		).toBeInTheDocument();
	});

	it("gives owners and reviewers a composer that names what it will do", async () => {
		const { project } = signedInWith("reviewer");
		renderApp(`/p/${project.id}`);

		const composer = await screen.findByRole("textbox", {
			name: "Start a review",
		});

		expect(composer).toHaveAttribute(
			"placeholder",
			"What decision do you need evidence for?",
		);
	});

	it("tells a viewer who can start a review instead of offering a composer", async () => {
		const { project } = signedInWith("viewer");
		renderApp(`/p/${project.id}`);

		expect(
			await screen.findByText("Only owners and reviewers can start a review."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("textbox", { name: "Start a review" }),
		).not.toBeInTheDocument();
	});

	it("keeps unsent text in memory when the person leaves and comes back", async () => {
		const { project } = signedInWith("owner");
		const { user, router } = renderApp(`/p/${project.id}`);
		await user.type(
			await screen.findByRole("textbox", { name: "Start a review" }),
			"Should we switch to cash?",
		);

		await router.navigate({ to: "/" });
		await screen.findByRole("heading", { level: 1, name: "Home" });
		await router.navigate({
			to: "/p/$projectId",
			params: { projectId: project.id },
		});

		expect(
			await screen.findByRole("textbox", { name: "Start a review" }),
		).toHaveValue("Should we switch to cash?");
		expect(window.localStorage.length).toBe(0);
	});

	it("says the same thing for a missing project and one the person cannot see", async () => {
		signedInWith("owner");
		renderApp("/p/00000000-0000-4000-8000-000000000000");

		expect(
			await screen.findByText(
				"This project doesn't exist or you don't have access.",
			),
		).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { project } = signedInWith("owner");
		const { container } = renderApp(`/p/${project.id}`);
		await screen.findByRole("textbox", { name: "Start a review" });

		await expectNoAccessibilityViolations(container);
	});
});

describe("starting a review from the workspace (UI_UX_SPEC §6.3, §5.3.1, §5.4)", () => {
	it("asks how thorough to be, Quick or Deep, with Deep chosen", async () => {
		const { project } = signedInWith("owner");
		renderApp(`/p/${project.id}`);

		const choice = await screen.findByRole("radiogroup", {
			name: "How thorough?",
		});

		expect(within(choice).getByRole("radio", { name: "Deep" })).toBeChecked();
		expect(
			within(choice).getByRole("radio", { name: "Quick" }),
		).toHaveAccessibleDescription(
			"About 90 papers, abstracts only, no questions first. Usually 10–20 minutes.",
		);
	});

	it("sends the question and the mode, then opens the review with the question as its first turn", async () => {
		const { api, project } = signedInWith("reviewer");
		const { user, router } = renderApp(`/p/${project.id}`);

		await user.click(await screen.findByRole("radio", { name: "Quick" }));
		await user.type(
			screen.getByRole("textbox", { name: "Start a review" }),
			"Should we switch to cash transfers in the camps near Gao?{Enter}",
		);

		const review = await waitFor(() => {
			const created = api.reviews.all[0];
			expect(created).toBeDefined();
			return created as NonNullable<typeof created>;
		});
		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}`),
		);
		await waitFor(() =>
			expect(
				api.requestsTo("POST", `/reviews/${review.id}/plan`)[0]?.body,
			).toEqual({ mode: "shallow" }),
		);
		const question = await screen.findByRole("article", { name: "Question" });
		expect(question).toHaveTextContent(
			"Should we switch to cash transfers in the camps near Gao?",
		);
		expect(question).toHaveTextContent("Quick search");
	});

	it("shows the question at once with Sending… while the plan is made", async () => {
		const { api, project } = signedInWith("owner");
		let reply: (() => void) | undefined;
		api.reviews.modelReply = new Promise<void>((resolve) => {
			reply = resolve;
		});
		const { user } = renderApp(`/p/${project.id}`);

		await user.type(
			await screen.findByRole("textbox", { name: "Start a review" }),
			"Cash or vouchers?{Enter}",
		);

		expect(await screen.findByText("Sending…")).toBeInTheDocument();
		expect(screen.getByText("Cash or vouchers?")).toBeInTheDocument();
		reply?.();
		expect(
			await screen.findByRole("article", { name: /Search planner/ }),
		).toBeInTheDocument();
	});

	it("keeps the question and offers to try again when the model server isn't answering", async () => {
		const { api, project } = signedInWith("owner");
		api.reviews.isModelDown = true;
		const { user } = renderApp(`/p/${project.id}`);

		await user.type(
			await screen.findByRole("textbox", { name: "Start a review" }),
			"Cash or vouchers?{Enter}",
		);

		const card = await screen.findByRole("article", {
			name: "The search plan couldn't be made",
		});
		expect(card).toHaveTextContent("The model server isn't answering.");
		expect(screen.getByRole("article", { name: "Question" })).toHaveTextContent(
			"Cash or vouchers?",
		);

		api.reviews.isModelDown = false;
		await user.click(within(card).getByRole("button", { name: "Try again" }));

		expect(
			await screen.findByRole("article", {
				name: "Search planner · 2 questions before the search",
			}),
		).toBeInTheDocument();
	});

	it("opens the latest review when the project already has one", async () => {
		const { api, project } = signedInWith("owner");
		const review = api.reviews.add(project.id, "Cash or vouchers?");
		api.reviews.startPlan(review, "shallow");
		const { router } = renderApp(`/p/${project.id}`);

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/r/${review.id}`),
		);
		expect(
			await screen.findByRole("article", { name: "Question" }),
		).toHaveTextContent("Cash or vouchers?");
	});
});
