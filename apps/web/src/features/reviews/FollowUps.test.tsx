import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import {
	EXAMPLE_ANSWER,
	MODEL_DOWN_ANSWER,
	NOTHING_FOUND,
} from "../../test/fakeQuestions";
import { renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function reviewed(
	options: { role?: "owner" | "reviewer" | "viewer"; hasBrief?: boolean } = {},
) {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject(
		"Cash vs vouchers, Gao",
		options.role ?? "owner",
	);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "done");
	if (options.hasBrief ?? true) {
		api.reviews.addBrief(review, run);
	}
	return {
		api,
		review,
		run,
		questions: api.reviews.questions,
		path: `/p/${project.id}/r/${review.id}`,
	};
}

const ASK = "Ask about the evidence";
const CHANGE = "Change the search";

async function composer() {
	return screen.findByRole("textbox", { name: ASK });
}

function questionPosts(reviewId: string) {
	return fakeApi().requestsTo("POST", `/reviews/${reviewId}/questions`);
}

function isBefore(first: Element, second: Element): boolean {
	return Boolean(
		first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
	);
}

describe("the box under the chat once a brief exists (UI_UX_SPEC §5.4, §6.12)", () => {
	it("offers asking about the evidence or changing the search, with asking chosen", async () => {
		const { path } = reviewed();
		renderApp(path);

		const ask = await screen.findByRole("button", { name: ASK });
		const change = screen.getByRole("button", { name: CHANGE });
		expect(ask).toHaveAttribute("aria-pressed", "true");
		expect(change).toHaveAttribute("aria-pressed", "false");
		expect(await composer()).toHaveAttribute("placeholder", "Ask a follow-up");
		expect(
			screen.getByText(
				"Answers use only the papers and documents in this project.",
			),
		).toBeInTheDocument();
	});

	it("keeps no choice when the review has no brief", async () => {
		const { path, review } = reviewed({ hasBrief: false });
		renderApp(path);

		await screen.findByRole("article", { name: "Run 1" });
		await waitFor(() =>
			expect(
				fakeApi().requestsTo("GET", `/reviews/${review.id}/brief`),
			).not.toHaveLength(0),
		);
		expect(screen.queryByRole("button", { name: ASK })).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: CHANGE }),
		).not.toBeInTheDocument();
	});

	it("switches the box away from questions when Change the search is chosen", async () => {
		const { path, review } = reviewed();
		const { user } = renderApp(path);
		await composer();

		await user.click(screen.getByRole("button", { name: CHANGE }));

		expect(screen.getByRole("button", { name: CHANGE })).toHaveAttribute(
			"aria-pressed",
			"true",
		);
		expect(screen.getByRole("button", { name: ASK })).toHaveAttribute(
			"aria-pressed",
			"false",
		);
		expect(
			screen.queryByRole("textbox", { name: ASK }),
		).not.toBeInTheDocument();

		await user.type(
			screen.getByRole("textbox", { name: CHANGE }),
			"Any refugees, not only Gao{Enter}",
		);

		await waitFor(() =>
			expect(
				fakeApi().requestsTo("POST", `/reviews/${review.id}/plan/changes`),
			).toHaveLength(1),
		);
		expect(questionPosts(review.id)).toHaveLength(0);
	});
});

describe("asking a follow-up question (UI_UX_SPEC §5.3.9, §11.3)", () => {
	it("shows your question at once, then what Humani-T is doing until the answer comes", async () => {
		const { path, questions, review } = reviewed();
		let release: () => void = () => undefined;
		questions.askReply = new Promise<void>((resolve) => {
			release = resolve;
		});
		const { user } = renderApp(path);

		await user.type(await composer(), "Do prices rise in Gao?{Enter}");

		const sending = await screen.findByRole("article", {
			name: "Follow-up question",
		});
		expect(sending).toHaveTextContent("Do prices rise in Gao?");
		expect(screen.getByText("Sending your question…")).toBeInTheDocument();

		release();

		await screen.findByText("Searching the evidence…");
		const asked = screen.getByRole("article", { name: "Follow-up question" });
		expect(asked).toHaveTextContent("masked location 1");
		expect(questionPosts(review.id).map((r) => r.body)).toEqual([
			{ question: "Do prices rise in Gao?", thread_id: null },
		]);
		expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
		expect(
			screen.getByText("Wait for the answer to your last question."),
		).toBeInTheDocument();

		questions.outcome = EXAMPLE_ANSWER;

		const answer = await screen.findByRole("article", { name: "Answer" });
		expect(answer).toHaveTextContent(
			"Staple prices rose by 5 to 12 percent in small markets after cash distributions.",
		);
		expect(answer).toHaveTextContent("reasoning, not a source");
		expect(answer).toHaveTextContent("1 source · 1 item masked");
		expect(
			screen.queryByText("Searching the evidence…"),
		).not.toBeInTheDocument();
	});

	it("opens a cited passage in the source panel", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do prices rise in Gao?");
		const { user } = renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		await user.click(within(answer).getByRole("button", { name: /Source P1/ }));

		const panel = await screen.findByRole("complementary", { name: "P1" });
		expect(panel).toHaveTextContent(
			"with fewer than ten traders, staple prices rose by 5 to 12 percent.",
		);
		expect(panel).toHaveTextContent("found by: cash transfers prices");

		await user.click(
			within(panel).getByRole("button", { name: "Close source" }),
		);
		expect(
			screen.queryByRole("complementary", { name: "P1" }),
		).not.toBeInTheDocument();
	});

	it("continues the latest thread", async () => {
		const { path, review, questions } = reviewed();
		const first = questions.addAnswered(review, "Do prices rise in Gao?");
		questions.outcome = EXAMPLE_ANSWER;
		const { user } = renderApp(path);
		await screen.findByRole("article", { name: "Answer" });

		await user.type(await composer(), "And in larger towns?{Enter}");

		await waitFor(() =>
			expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
				2,
			),
		);
		expect(questionPosts(review.id).map((r) => r.body)).toEqual([
			{ question: "And in larger towns?", thread_id: first.thread_id },
		]);
	});

	it("says plainly when nothing in the project answers the question", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Does cash help fishing?", {
			kind: "nothing",
		});
		renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		expect(answer).toHaveTextContent(NOTHING_FOUND);
		expect(
			within(answer).queryByRole("button", { name: /Source/ }),
		).not.toBeInTheDocument();
	});

	it("shows a failed answer as a problem, and Try again asks once more in the same thread", async () => {
		const { path, review, questions } = reviewed();
		questions.outcome = MODEL_DOWN_ANSWER;
		const { user } = renderApp(path);

		await user.type(await composer(), "Do prices rise in Gao?{Enter}");

		const failed = await screen.findByRole("article", {
			name: "The answer couldn't be made",
		});
		expect(failed).toHaveAttribute("data-role", "problem");
		expect(failed).toHaveTextContent(
			"The model server isn't answering. Ask again in a minute.",
		);

		questions.outcome = EXAMPLE_ANSWER;
		await user.click(within(failed).getByRole("button", { name: "Try again" }));

		await screen.findByRole("article", { name: "Answer" });
		expect(questionPosts(review.id).map((r) => r.body)).toEqual([
			{ question: "Do prices rise in Gao?", thread_id: null },
			{
				question: "Do prices rise in Gao?",
				thread_id: questions.threads[0]?.id,
			},
		]);
	});
});

describe("follow-ups in the conversation (UI_UX_SPEC §6.12)", () => {
	it("places each question after the run it follows", async () => {
		const { api, path, review, run, questions } = reviewed();
		run.created_at = "2026-11-03T10:51:00Z";
		questions.addAnswered(
			review,
			"Do prices rise in Gao?",
			EXAMPLE_ANSWER,
			"2026-11-03T11:30:00Z",
		);
		const second = api.reviews.addRun(review, "running");
		second.created_at = "2026-11-03T12:00:00Z";
		renderApp(path);

		const asked = await screen.findByRole("article", {
			name: "Follow-up question",
		});
		const firstRun = screen.getByRole("link", { name: "Open run 1" });
		const latestRun = screen.getByRole("article", { name: "Run 2" });
		expect(isBefore(firstRun, asked)).toBe(true);
		expect(isBefore(asked, latestRun)).toBe(true);
	});

	it("lets a viewer read the answers but not ask, and says why", async () => {
		const { path, review, questions } = reviewed({ role: "viewer" });
		questions.addAnswered(review, "Do prices rise in Gao?");
		renderApp(path);

		await screen.findByRole("article", { name: "Answer" });
		expect(
			screen.getByText(
				"You can read the answers. Only owners and reviewers can ask.",
			),
		).toBeInTheDocument();
		expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: ASK })).not.toBeInTheDocument();
	});

	it("has no accessibility problems with an answer and the choice above the box", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do prices rise in Gao?");
		renderApp(path);
		await screen.findByRole("article", { name: "Answer" });
		await composer();

		await expectNoAccessibilityViolations();
	});
});
