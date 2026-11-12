import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import {
	ALREADY_ACCEPTED,
	draftVersion,
	FIRST_DRAFT,
} from "../../test/fakeReviews";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const QUESTION = "Should we switch to cash transfers in the camps near Gao?";

function reviewIn(
	mode: "deep" | "shallow" | null,
	role: "owner" | "reviewer" | "viewer" = "owner",
) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, QUESTION);
	if (mode !== null) {
		api.reviews.startPlan(review, mode);
	}
	return { api, project, review, path: `/p/${project.id}/r/${review.id}` };
}

describe("the privacy receipt (UI_UX_SPEC §5.3.2, §8.5)", () => {
	it("shows what the model received, masked items as pills, counts by type, never the original", async () => {
		const { path } = reviewIn("deep");
		renderApp(path);

		const receipt = await screen.findByRole("region", {
			name: "What the model received",
		});

		expect(screen.getByText("Sent with 1 item masked")).toBeInTheDocument();
		expect(receipt).toHaveTextContent(/Sent to \S+/);
		expect(within(receipt).getByText("Location 1")).toBeInTheDocument();
		expect(within(receipt).getByText("masked location 1")).toBeInTheDocument();
		expect(receipt).not.toHaveTextContent(/[⟨⟩]/);
		expect(
			within(receipt).getByRole("list", { name: "Masked" }),
		).toHaveTextContent("1 location");
		expect(receipt).toHaveTextContent("Double-check codes and numbers.");
		expect(receipt).not.toHaveTextContent("Gao");
	});

	it("says plainly when the text went out but no answer came back", async () => {
		const { api, review, path } = reviewIn("deep");
		api.reviews.failDelivery(review);
		renderApp(path);

		const receipt = await screen.findByRole("region", {
			name: "What the model received",
		});

		expect(receipt).toHaveTextContent("Sent, but no answer came back.");
	});

	it("says the text was sent as written when nothing was masked, folded to one line", async () => {
		const { api, project } = reviewIn(null);
		const plain = api.reviews.add(project.id, "Cash or vouchers in camps?");
		api.reviews.startPlan(plain, "shallow");
		const second = api.reviews.add(project.id, "Do vouchers cost more to run?");
		api.reviews.startPlan(second, "shallow");
		const { user } = renderApp(`/p/${project.id}/r/${second.id}`);

		const toggle = await screen.findByRole("button", {
			name: "Show what was sent",
		});
		expect(
			screen.getByText("Sent as written · nothing to mask"),
		).toBeInTheDocument();
		expect(toggle).toHaveAttribute("aria-expanded", "false");

		await user.click(toggle);
		expect(
			screen.getByRole("region", { name: "What the model received" }),
		).toHaveTextContent("Do vouchers cost more to run?");
	});
});

describe("who said what in the chat", () => {
	it("marks the person's own question as theirs, and the planner's cards as the planner's", async () => {
		const { path } = reviewIn("deep");
		renderApp(path);

		const question = await screen.findByRole("article", { name: "Question" });
		const planner = await screen.findByRole("article", {
			name: "Search planner · 2 questions before the search",
		});

		expect(question).toHaveAttribute("data-role", "person");
		expect(within(question).getByText("You")).toBeInTheDocument();
		expect(planner).toHaveAttribute("data-role", "humani-t");
	});
});

describe("clarifying questions (UI_UX_SPEC §5.3.3, §6.4)", () => {
	it("asks one question at a time with its suggestions as chips", async () => {
		const { path } = reviewIn("deep");
		renderApp(path);

		const card = await screen.findByRole("article", {
			name: "Search planner · 2 questions before the search",
		});

		expect(card).toHaveTextContent("1 of 2");
		expect(card).toHaveTextContent("Which outcomes matter most?");
		expect(
			within(card).getByRole("button", { name: "Food security" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("textbox", { name: "Answering question 1 of 2" }),
		).toHaveAttribute("placeholder", "Your answer, or pick a suggestion above");
	});

	it("fills the answer from a chip, lets the person edit it, and sends it", async () => {
		const { api, path, review } = reviewIn("deep");
		const { user } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · 2 questions before the search",
		});

		await user.click(
			within(card).getByRole("button", { name: "Market prices" }),
		);
		const box = screen.getByRole("textbox", {
			name: "Answering question 1 of 2",
		});
		expect(box).toHaveValue("Market prices");
		expect(box).toHaveFocus();
		await user.type(box, " in Gao");
		await user.click(within(card).getByRole("button", { name: "Answer" }));

		await waitFor(() =>
			expect(card).toHaveTextContent("Which places should the search cover?"),
		);
		expect(
			api.requestsTo("POST", `/reviews/${review.id}/plan/answers`)[0]?.body,
		).toEqual({
			answers: [{ question_id: "q1", answer: "Market prices in Gao" }],
			draft_now: false,
		});
	});

	it("skips one question, then all the rest, and folds to one line, counting answers and skips, once a draft is made", async () => {
		const { api, path, review } = reviewIn("deep");
		const { user } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · 2 questions before the search",
		});

		await user.click(
			within(card).getByRole("button", { name: "Skip this one" }),
		);
		await waitFor(() => expect(card).toHaveTextContent("2 of 2"));
		await user.click(
			within(card).getByRole("button", { name: "Skip all and draft now" }),
		);

		expect(
			await screen.findByText("2 clarifying questions · 0 answered, 2 skipped"),
		).toBeInTheDocument();
		expect(
			screen.getByRole("article", { name: "Search planner · Draft 1" }),
		).toBeInTheDocument();
		expect(
			api
				.requestsTo("POST", `/reviews/${review.id}/plan/answers`)
				.map((r) => r.body),
		).toEqual([
			{ answers: [{ question_id: "q1", answer: null }], draft_now: false },
			{ answers: [], draft_now: true },
		]);
	});
});

describe("the search draft card (UI_UX_SPEC §5.3.4)", () => {
	it("sums the draft up, one line per category, with the main action and the notes count", async () => {
		const { path } = reviewIn("shallow");
		renderApp(path);

		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		expect(card).toHaveTextContent("3 categories · 4 concepts");
		const lines = within(card).getAllByRole("listitem");
		expect(lines.map((line) => line.textContent)).toEqual([
			"intervention: cash transfers or vouchers",
			"outcome: food security",
			"setting: displacement",
		]);
		expect(card).toHaveTextContent("1 change made automatically");
		expect(
			within(card).getByRole("button", { name: "Accept and search" }),
		).toBeInTheDocument();
		expect(
			within(card).getByRole("link", { name: "Open draft" }),
		).toBeInTheDocument();
	});

	it("asks for changes through the composer, with a hint", async () => {
		const { path } = reviewIn("shallow");
		const { user } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		await user.click(
			within(card).getByRole("button", { name: "Ask for changes" }),
		);

		const box = screen.getByRole("textbox", {
			name: "Changing the search · draft 1",
		});
		expect(box).toHaveFocus();
		expect(box).toHaveAttribute(
			"placeholder",
			"Say what to change in the search",
		);
		expect(box).toHaveAccessibleDescription(
			expect.stringContaining(
				"Say what to change, for example: add 'mobile money' as a synonym for cash.",
			),
		);
	});

	it("sends a change request as a turn, and folds the replaced draft", async () => {
		const { api, path, review } = reviewIn("shallow");
		const { user } = renderApp(path);
		const box = await screen.findByRole("textbox", {
			name: "Changing the search · draft 1",
		});

		await user.type(box, "Add mobile money as a synonym for cash.{Enter}");

		expect(
			await screen.findByRole("article", { name: "Search planner · Draft 2" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("article", { name: "Change request" }),
		).toHaveTextContent("Add mobile money as a synonym for cash.");
		expect(
			screen.getByText("Draft 1 · replaced by draft 2"),
		).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/reviews/${review.id}/plan/changes`)[0]?.body,
		).toEqual({
			request: "Add mobile money as a synonym for cash.",
			based_on_version: 1,
		});
	});

	it("accepts the draft and starts the search: the whole pipeline, then a run strip", async () => {
		const { api, path, review } = reviewIn("shallow");
		const { user } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		await user.click(
			within(card).getByRole("button", { name: "Accept and search" }),
		);

		expect(
			await screen.findByText(
				"Search plan accepted · version 1 · 3 categories",
			),
		).toBeInTheDocument();
		expect(await screen.findByText("Starting the run")).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/reviews/${review.id}/plan/accept`)[0]?.body,
		).toEqual({
			version: 1,
		});
		expect(
			api.requestsTo("POST", `/reviews/${review.id}/runs`)[0]?.body,
		).toEqual({
			stages: [
				"queries",
				"discover",
				"ingest",
				"mask",
				"screen",
				"export",
				"answer",
			],
		});
	});

	it("says so, and shows the latest, when someone else accepted first", async () => {
		const { api, path, review } = reviewIn("shallow");
		const { user } = renderApp(path);
		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});
		api.reviews.accept(review);

		await user.click(
			within(card).getByRole("button", { name: "Accept and search" }),
		);

		expect(
			await screen.findByText(
				"Search plan accepted · version 1 · 3 categories",
			),
		).toBeInTheDocument();
		expect(screen.getByText(ALREADY_ACCEPTED)).toBeInTheDocument();
	});

	it("says a person's own terms are used exactly as given, and offers no model changes", async () => {
		const { api, path, review } = reviewIn(null);
		const plan = api.reviews.startPlan(review, "shallow");
		plan.versions[0] = draftVersion(1, FIRST_DRAFT, { is_strict: true });
		renderApp(path);

		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		expect(card).toHaveTextContent("Your terms, used exactly as given");
		expect(
			within(card).queryByRole("button", { name: "Ask for changes" }),
		).not.toBeInTheDocument();
	});
});

describe("what a viewer sees (UI_UX_SPEC §3.3)", () => {
	it("reads the conversation but is offered no actions and no composer", async () => {
		const { path } = reviewIn("shallow", "viewer");
		renderApp(path);

		const card = await screen.findByRole("article", {
			name: "Search planner · Draft 1",
		});

		expect(
			within(card).queryByRole("button", { name: "Accept and search" }),
		).not.toBeInTheDocument();
		expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
		expect(
			screen.getByText(
				"Only owners and reviewers can change or accept the search plan.",
			),
		).toBeInTheDocument();
	});
});

describe("a review whose plan was never made", () => {
	it("offers to make it, choosing Quick or Deep", async () => {
		const { api, path, review } = reviewIn(null);
		const { user } = renderApp(path);

		const card = await screen.findByRole("article", {
			name: "The search plan hasn't been made yet",
		});
		await user.click(within(card).getByRole("radio", { name: "Quick" }));
		await user.click(
			within(card).getByRole("button", { name: "Make the search plan" }),
		);

		expect(
			await screen.findByRole("article", { name: "Search planner · Draft 1" }),
		).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/reviews/${review.id}/plan`)[0]?.body,
		).toEqual({
			mode: "shallow",
		});
	});
});

describe("the review conversation", () => {
	it("has no accessibility violations", async () => {
		const { path } = reviewIn("deep");
		const { container } = renderApp(path);
		await screen.findByRole("article", {
			name: "Search planner · 2 questions before the search",
		});

		await expectNoAccessibilityViolations(container);
	});

	it("has no accessibility violations with a draft", async () => {
		const { path } = reviewIn("shallow");
		const { container } = renderApp(path);
		await screen.findByRole("article", { name: "Search planner · Draft 1" });

		await expectNoAccessibilityViolations(container);
	});
});
