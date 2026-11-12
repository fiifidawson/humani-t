import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import {
	answerCiting,
	EXAMPLE_ANSWER,
	type FakeOutcome,
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

const NEW_THREAD = "New thread";

/** Two threads: the first with two questions, the second with one. */
function twoThreads() {
	const setup = reviewed();
	const { review, questions } = setup;
	const first = questions.addAnswered(
		review,
		"Do cash transfers raise prices?",
		EXAMPLE_ANSWER,
		"2026-11-03T11:30:00Z",
	);
	questions.addAnswered(
		review,
		"And in larger towns?",
		EXAMPLE_ANSWER,
		"2026-11-03T11:35:00Z",
		first.thread_id,
	);
	const second = questions.addAnswered(
		review,
		"What do vouchers cost to run?",
		EXAMPLE_ANSWER,
		"2026-11-03T11:40:00Z",
	);
	return { ...setup, first, second };
}

describe("threads of follow-up questions (UI_UX_SPEC §6.12)", () => {
	it("offers no New thread before the first question, since that starts one anyway", async () => {
		const { path } = reviewed();
		renderApp(path);
		await composer();

		expect(
			screen.queryByRole("button", { name: NEW_THREAD }),
		).not.toBeInTheDocument();
	});

	it("offers New thread only while asking is chosen", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do cash transfers raise prices?");
		const { user } = renderApp(path);
		await screen.findByRole("article", { name: "Answer" });

		const newThread = await screen.findByRole("button", { name: NEW_THREAD });
		expect(newThread).toHaveAttribute("aria-pressed", "false");

		await user.click(screen.getByRole("button", { name: CHANGE }));

		expect(
			screen.queryByRole("button", { name: NEW_THREAD }),
		).not.toBeInTheDocument();
	});

	it("starts a fresh thread with the next question, then continues that one", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do cash transfers raise prices?");
		questions.outcome = EXAMPLE_ANSWER;
		const { user } = renderApp(path);
		await screen.findByRole("article", { name: "Answer" });

		await user.click(await screen.findByRole("button", { name: NEW_THREAD }));

		expect(screen.getByRole("button", { name: NEW_THREAD })).toHaveAttribute(
			"aria-pressed",
			"true",
		);
		expect(await composer()).toHaveAttribute(
			"placeholder",
			"Ask a question to start a new thread",
		);

		await user.type(await composer(), "What do vouchers cost to run?{Enter}");

		await waitFor(() =>
			expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
				2,
			),
		);
		expect(screen.getByRole("button", { name: NEW_THREAD })).toHaveAttribute(
			"aria-pressed",
			"false",
		);

		await user.type(await composer(), "And in camps?{Enter}");

		await waitFor(() =>
			expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
				3,
			),
		);
		const fresh = questions.threads.find((t) =>
			t.questions.some(
				(q) => q.question_sent === "What do vouchers cost to run?",
			),
		);
		expect(questionPosts(review.id).map((r) => r.body)).toEqual([
			{ question: "What do vouchers cost to run?", thread_id: null },
			{ question: "And in camps?", thread_id: fresh?.id },
		]);
	});

	it("goes back to continuing the latest thread when New thread is pressed again", async () => {
		const { path, review, questions } = reviewed();
		const first = questions.addAnswered(
			review,
			"Do cash transfers raise prices?",
		);
		questions.outcome = EXAMPLE_ANSWER;
		const { user } = renderApp(path);
		await screen.findByRole("article", { name: "Answer" });

		await user.click(await screen.findByRole("button", { name: NEW_THREAD }));
		await user.click(screen.getByRole("button", { name: NEW_THREAD }));
		await user.type(await composer(), "And in larger towns?{Enter}");

		await waitFor(() => expect(questionPosts(review.id)).toHaveLength(1));
		expect(questionPosts(review.id)[0]?.body).toEqual({
			question: "And in larger towns?",
			thread_id: first.thread_id,
		});
	});

	it("lets a new thread start while the last answer is still coming", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do cash transfers raise prices?");
		const { user } = renderApp(path);
		await screen.findByRole("article", { name: "Answer" });

		await user.type(await composer(), "And in larger towns?{Enter}");
		await screen.findByText("Wait for the answer to your last question.");

		await user.click(screen.getByRole("button", { name: NEW_THREAD }));

		expect(
			screen.queryByText("Wait for the answer to your last question."),
		).not.toBeInTheDocument();
		await user.type(await composer(), "What do vouchers cost to run?{Enter}");
		await waitFor(() => expect(questionPosts(review.id)).toHaveLength(2));
		expect(questionPosts(review.id)[1]?.body).toEqual({
			question: "What do vouchers cost to run?",
			thread_id: null,
		});
	});

	it("marks where each later thread starts in the conversation", async () => {
		const { path } = twoThreads();
		renderApp(path);

		await waitFor(() =>
			expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
				3,
			),
		);
		const dividers = screen.getAllByRole("separator", { name: NEW_THREAD });
		expect(dividers).toHaveLength(1);
		const [, secondOfFirst, firstOfSecond] = screen.getAllByRole("article", {
			name: "Follow-up question",
		});
		const divider = dividers[0];
		if (
			divider === undefined ||
			secondOfFirst === undefined ||
			firstOfSecond === undefined
		) {
			throw new Error("Expected three questions and a divider");
		}
		expect(secondOfFirst).toHaveTextContent("And in larger towns?");
		expect(firstOfSecond).toHaveTextContent("What do vouchers cost to run?");
		expect(isBefore(secondOfFirst, divider)).toBe(true);
		expect(isBefore(divider, firstOfSecond)).toBe(true);
	});

	it("lists the threads with their first question, size and time, newest first", async () => {
		const { path } = twoThreads();
		const { user } = renderApp(path);

		const toggle = await screen.findByRole("button", { name: "2 threads" });
		expect(toggle).toHaveAttribute("aria-expanded", "false");
		await user.click(toggle);

		const list = screen.getByRole("list", { name: "Threads" });
		const items = within(list).getAllByRole("button");
		expect(items).toHaveLength(2);
		expect(items[0]).toHaveTextContent("What do vouchers cost to run?");
		expect(items[0]).toHaveTextContent("1 question");
		expect(items[1]).toHaveTextContent("Do cash transfers raise prices?");
		expect(items[1]).toHaveTextContent("2 questions");
		expect(items[1]).toHaveTextContent(/3 Nov/);
	});

	it("jumps to a thread's first question from the list", async () => {
		const { path } = twoThreads();
		const { user } = renderApp(path);

		await user.click(await screen.findByRole("button", { name: "2 threads" }));
		const list = screen.getByRole("list", { name: "Threads" });
		await user.click(
			within(list).getByRole("button", {
				name: /Do cash transfers raise prices\?/,
			}),
		);

		const first = screen.getAllByRole("article", {
			name: "Follow-up question",
		})[0];
		expect(first).toHaveTextContent("Do cash transfers raise prices?");
		expect(document.activeElement).toContainElement(first ?? null);
	});

	it("lets a viewer see the thread list too", async () => {
		const { path, review, questions } = reviewed({ role: "viewer" });
		questions.addAnswered(review, "Do cash transfers raise prices?");
		renderApp(path);

		expect(
			await screen.findByRole("button", { name: "1 thread" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: NEW_THREAD }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility problems with threads, their divider and the list open", async () => {
		const { path } = twoThreads();
		const { user } = renderApp(path);
		await user.click(await screen.findByRole("button", { name: "2 threads" }));
		await composer();

		await expectNoAccessibilityViolations();
	});
});

describe("an answer's source panel (UI_UX_SPEC §6.10, §6.12)", () => {
	it("shows the cited paper's title and year", async () => {
		const { api, path, review, run, questions } = reviewed();
		const paper = api.reviews.addPaper(review, run, {
			title: "Cash transfers and market prices in Mopti",
			year: 2022,
		});
		questions.addAnswered(
			review,
			"Do cash transfers raise prices?",
			answerCiting(paper.paper_id),
		);
		const { user } = renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		await user.click(within(answer).getByRole("button", { name: /Source P1/ }));

		const panel = await screen.findByRole("complementary", { name: "P1" });
		expect(
			await within(panel).findByText(
				"Cash transfers and market prices in Mopti",
			),
		).toBeInTheDocument();
		expect(panel).toHaveTextContent("2022");
		expect(panel).toHaveTextContent(
			"with fewer than ten traders, staple prices rose by 5 to 12 percent.",
		);
	});

	it("gives focus back to the citation chip when it closes", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do cash transfers raise prices?");
		const { user } = renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		const chip = within(answer).getByRole("button", { name: /Source P1/ });
		await user.click(chip);
		const panel = await screen.findByRole("complementary", { name: "P1" });
		await user.click(
			within(panel).getByRole("button", { name: "Close source" }),
		);

		expect(chip).toHaveFocus();

		await user.click(chip);
		await screen.findByRole("complementary", { name: "P1" });
		await user.keyboard("{Escape}");

		expect(
			screen.queryByRole("complementary", { name: "P1" }),
		).not.toBeInTheDocument();
		expect(chip).toHaveFocus();
	});
});

describe("when nothing in the project answers (UI_UX_SPEC §6.12)", () => {
	it("offers Change the search, starting from the question", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Does cash help fishing?", {
			kind: "nothing",
		});
		const { user } = renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		await user.click(within(answer).getByRole("button", { name: CHANGE }));

		const box = screen.getByRole("textbox", { name: CHANGE });
		expect(box).toHaveValue("Does cash help fishing?");
		expect(box).toHaveFocus();
		expect(
			within(
				screen.getByRole("group", { name: "What the box does" }),
			).getByRole("button", { name: CHANGE }),
		).toHaveAttribute("aria-pressed", "true");
	});

	it("keeps what you had typed rather than replace it", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Does cash help fishing?", {
			kind: "nothing",
		});
		const { user } = renderApp(path);
		const answer = await screen.findByRole("article", { name: "Answer" });
		await user.type(await composer(), "Add fisheries as a theme");

		await user.click(within(answer).getByRole("button", { name: CHANGE }));

		expect(screen.getByRole("textbox", { name: CHANGE })).toHaveValue(
			"Add fisheries as a theme",
		);
	});

	it("does not offer it with an answer that found something", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Does cash help fishing?", {
			kind: "nothing",
		});
		questions.addAnswered(
			review,
			"Do cash transfers raise prices?",
			EXAMPLE_ANSWER,
			"2026-11-03T11:40:00Z",
		);
		renderApp(path);

		await waitFor(() =>
			expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
				2,
			),
		);
		const [nothing, found] = screen.getAllByRole("article", {
			name: "Answer",
		});
		expect(nothing).toHaveTextContent(NOTHING_FOUND);
		expect(
			within(nothing ?? document.body).getByRole("button", { name: CHANGE }),
		).toBeInTheDocument();
		expect(
			within(found ?? document.body).queryByRole("button", { name: CHANGE }),
		).not.toBeInTheDocument();
	});

	it("does not offer it to a viewer, who cannot change the search", async () => {
		const { path, review, questions } = reviewed({ role: "viewer" });
		questions.addAnswered(review, "Does cash help fishing?", {
			kind: "nothing",
		});
		renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		expect(
			within(answer).queryByRole("button", { name: CHANGE }),
		).not.toBeInTheDocument();
	});
});

/** `count` words, `prefix1` to `prefixN`, so a test can tell where a cut fell. */
function words(prefix: string, count: number): string {
	return Array.from({ length: count }, (_, i) => `${prefix}${i + 1}`).join(" ");
}

/** The example answer with an evidence claim of `evidence` words and reasoning of `reasoning`. */
function answerOf(evidence: number, reasoning: number): FakeOutcome {
	if (EXAMPLE_ANSWER.kind !== "answer") {
		throw new Error("EXAMPLE_ANSWER must be an answer");
	}
	return {
		...EXAMPLE_ANSWER,
		claims: [
			{
				text: words("alpha", evidence),
				support: "evidence",
				citations: [{ tag: "P1", source_kind: "paper" }],
			},
			{ text: words("beta", reasoning), support: "hypothesis", citations: [] },
		],
	};
}

describe("a long answer (UI_UX_SPEC §5.3.9)", () => {
	it("shows the first 60 words, and Read all shows the rest in place", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do prices rise in Gao?", answerOf(40, 30));
		const { user } = renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		expect(answer).toHaveTextContent("alpha40");
		expect(answer).toHaveTextContent("beta20…");
		expect(answer).not.toHaveTextContent("beta21");
		const readAll = within(answer).getByRole("button", { name: "Read all" });
		expect(readAll).toHaveAttribute("aria-expanded", "false");

		await user.click(readAll);

		expect(answer).toHaveTextContent("beta30");
		expect(answer).not.toHaveTextContent("…");
		expect(readAll).toHaveAttribute("aria-expanded", "true");
		expect(readAll).toHaveFocus();
	});

	it("shows an answer of 60 words whole, with no Read all", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do prices rise in Gao?", answerOf(40, 20));
		renderApp(path);

		const answer = await screen.findByRole("article", { name: "Answer" });
		expect(answer).toHaveTextContent("beta20");
		expect(
			within(answer).queryByRole("button", { name: "Read all" }),
		).not.toBeInTheDocument();
	});

	it("keeps its citation chips working before and after Read all", async () => {
		const { path, review, questions } = reviewed();
		questions.addAnswered(review, "Do prices rise in Gao?", answerOf(70, 5));
		const { user } = renderApp(path);
		const answer = await screen.findByRole("article", { name: "Answer" });

		await user.click(within(answer).getByRole("button", { name: /Source P1/ }));
		const panel = await screen.findByRole("complementary", { name: "P1" });
		await user.click(
			within(panel).getByRole("button", { name: "Close source" }),
		);
		await user.click(within(answer).getByRole("button", { name: "Read all" }));
		await user.click(within(answer).getByRole("button", { name: /Source P1/ }));

		expect(
			await screen.findByRole("complementary", { name: "P1" }),
		).toHaveTextContent("staple prices rose by 5 to 12 percent.");
	});
});
