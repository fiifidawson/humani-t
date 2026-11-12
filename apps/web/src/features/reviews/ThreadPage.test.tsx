import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { EXAMPLE_ANSWER } from "../../test/fakeQuestions";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const ASK = "Ask about the evidence";

/** A review with two threads: the older with two questions, the newer with one. */
function twoThreads(role: "owner" | "reviewer" | "viewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	api.reviews.addBrief(review, api.reviews.addRun(review, "done"));
	const { questions } = api.reviews;
	const older = questions.addAnswered(
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
		older.thread_id,
	);
	questions.addAnswered(
		review,
		"What do vouchers cost to run?",
		EXAMPLE_ANSWER,
		"2026-11-03T11:40:00Z",
	);
	return {
		api,
		project,
		review,
		questions,
		reviewPath: `/p/${project.id}/r/${review.id}`,
		path: `/p/${project.id}/ask/${older.thread_id}`,
		threadId: older.thread_id,
	};
}

async function answers(count: number) {
	await waitFor(() =>
		expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
			count,
		),
	);
}

describe("a thread's own page (UI_UX_SPEC §6.12)", () => {
	it("shows the thread's questions and answers in order, and no other thread's", async () => {
		const { path } = twoThreads();
		renderApp(path);

		await answers(2);
		const asked = screen.getAllByRole("article", {
			name: "Follow-up question",
		});
		expect(asked).toHaveLength(2);
		expect(asked[0]).toHaveTextContent("Do cash transfers raise prices?");
		expect(asked[1]).toHaveTextContent("And in larger towns?");
		expect(
			screen.queryByText("What do vouchers cost to run?"),
		).not.toBeInTheDocument();
	});

	it("links back to the review", async () => {
		const { path, reviewPath } = twoThreads();
		const { user, router } = renderApp(path);

		await user.click(
			await screen.findByRole("link", { name: "Back to the review" }),
		);

		await waitFor(() => expect(currentPath(router)).toBe(reviewPath));
	});

	it("asks in this thread, though it is not the latest, and waits for the answer", async () => {
		const { path, review, questions, threadId } = twoThreads();
		const { user } = renderApp(path);
		await answers(2);

		await user.type(
			await screen.findByRole("textbox", { name: ASK }),
			"What about Gao?{Enter}",
		);

		await screen.findByText("Searching the evidence…");
		expect(
			fakeApi()
				.requestsTo("POST", `/reviews/${review.id}/questions`)
				.map((r) => r.body),
		).toEqual([{ question: "What about Gao?", thread_id: threadId }]);
		expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
		expect(
			screen.getByText("Wait for the answer to your last question."),
		).toBeInTheDocument();

		questions.outcome = EXAMPLE_ANSWER;

		await answers(3);
		expect(
			screen.queryByText("Searching the evidence…"),
		).not.toBeInTheDocument();
	});

	it("lets a reviewer ask", async () => {
		const { path } = twoThreads("reviewer");
		renderApp(path);

		expect(
			await screen.findByRole("textbox", { name: ASK }),
		).toBeInTheDocument();
	});

	it("lets a viewer read the thread but not ask, and says why", async () => {
		const { path } = twoThreads("viewer");
		renderApp(path);

		await answers(2);
		expect(
			screen.getByText(
				"You can read the answers. Only owners and reviewers can ask.",
			),
		).toBeInTheDocument();
		expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
	});

	it("says a missing thread doesn't exist", async () => {
		const { project } = twoThreads();
		renderApp(`/p/${project.id}/ask/00000000-0000-4000-8000-000000000000`);

		expect(
			await screen.findByText(
				"This project doesn't exist or you don't have access.",
			),
		).toBeInTheDocument();
	});

	it("says the same of a thread in a project the person cannot see", async () => {
		const { api, project, threadId } = twoThreads();
		const mine = api.addProject("Shelter, Mopti");
		api.projects.splice(api.projects.indexOf(project), 1);
		renderApp(`/p/${mine.id}/ask/${threadId}`);

		expect(
			await screen.findByText(
				"This project doesn't exist or you don't have access.",
			),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Do cash transfers raise prices?"),
		).not.toBeInTheDocument();
	});

	it("opens from the thread list under the conversation", async () => {
		const { path, reviewPath } = twoThreads();
		const { user, router } = renderApp(reviewPath);

		await user.click(await screen.findByRole("button", { name: "2 threads" }));
		const list = screen.getByRole("list", { name: "Threads" });
		const older = within(list).getAllByRole("listitem")[1];
		if (older === undefined) {
			throw new Error("Expected two threads in the list");
		}
		expect(older).toHaveTextContent("Do cash transfers raise prices?");
		await user.click(within(older).getByRole("link", { name: "Open" }));

		await waitFor(() => expect(currentPath(router)).toBe(path));
		await answers(2);
	});

	it("has no accessibility problems", async () => {
		const { path } = twoThreads();
		renderApp(path);
		await answers(2);
		await screen.findByRole("textbox", { name: ASK });

		await expectNoAccessibilityViolations();
	});
});
