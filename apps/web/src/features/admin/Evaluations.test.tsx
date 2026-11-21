import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { finishedEvaluation } from "../../test/fakeEvaluations";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

const QUESTIONS =
	"Do cash transfers raise prices?\nWhich markets recover first?\n";
const ANSWERS =
	'{"answer": "Yes, by a tenth."}\n{"answer": "Markets near roads."}\n';

/** An administrator on a deployment that has turned evaluations on. */
function admin() {
	const api = fakeApi();
	api.isEvaluationsOn = true;
	const user = api.addUser({ hasTotp: true });
	user.is_admin = true;
	api.signInAs(user);
	const project = api.addProject("Cash vs vouchers, Gao", "reviewer");
	return { api, project };
}

function textFile(name: string, content: string): File {
	return new File([content], name, { type: "text/plain" });
}

describe("the admin area's link (UI_UX_SPEC §6.15)", () => {
	it("shows Evaluations in the rail to administrators", async () => {
		admin();
		renderApp("/");

		const nav = await screen.findByRole("navigation", { name: "Main" });
		expect(
			await within(nav).findByRole("link", { name: "Evaluations" }),
		).toHaveAttribute("href", "/admin/evaluations");
	});

	it("leaves the link out, for administrators too, while the deployment has evaluations off", async () => {
		const { api } = admin();
		api.isEvaluationsOn = false;
		renderApp("/");

		const nav = await screen.findByRole("navigation", { name: "Main" });
		await within(nav).findByRole("link", { name: "Home" });
		expect(
			within(nav).queryByRole("link", { name: "Evaluations" }),
		).not.toBeInTheDocument();
	});

	it("leaves the link out for everyone else", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		renderApp("/");

		const nav = await screen.findByRole("navigation", { name: "Main" });
		await within(nav).findByRole("link", { name: "Home" });
		expect(
			within(nav).queryByRole("link", { name: "Evaluations" }),
		).not.toBeInTheDocument();
	});

	it("shows the API's refusal to someone who is not an administrator", async () => {
		const api = fakeApi();
		api.signInAs(api.addUser({ hasTotp: true }));
		renderApp("/admin/evaluations");

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Administrators only.",
		);
	});
});

describe("the list of evaluations", () => {
	it("lists each evaluation with its project and how far it has got", async () => {
		const { api, project } = admin();
		const done = api.evaluations.add(finishedEvaluation(project.id));
		api.evaluations.add(
			finishedEvaluation(project.id, {
				status: "running",
				question_count: 4,
				answered_count: 1,
				finished_at: null,
				created_at: "2026-11-21T10:00:00Z",
			}),
		);
		const { container } = renderApp("/admin/evaluations");

		const list = await screen.findByRole("list", { name: "Evaluations" });
		const rows = within(list).getAllByRole("listitem");
		expect(rows[0]).toHaveTextContent("Answered 1 of 4 questions");
		expect(rows[1]).toHaveTextContent("Finished");
		expect(
			within(rows[1] ?? document.body).getByRole("link", {
				name: /Cash vs vouchers, Gao/,
			}),
		).toHaveAttribute("href", `/admin/evaluations/${done.id}`);
		await expectNoAccessibilityViolations(container);
	});

	it("says when there is none yet", async () => {
		admin();
		renderApp("/admin/evaluations");

		expect(await screen.findByText(/No evaluations yet/)).toBeInTheDocument();
	});
});

describe("starting an evaluation", () => {
	it("sends the chosen project, files and judge, then opens the evaluation", async () => {
		const { api, project } = admin();
		const { user, router, container } = renderApp("/admin/evaluations/new");

		await screen.findByRole("option", { name: project.name });
		await user.selectOptions(screen.getByLabelText("Project"), project.id);
		await user.upload(
			screen.getByLabelText("Question file"),
			textFile("questions.txt", QUESTIONS),
		);
		await user.click(
			screen.getByRole("button", { name: "Add another system's answers" }),
		);
		await user.type(screen.getByLabelText("System name"), "System B");
		await user.upload(
			screen.getByLabelText("Answers file"),
			textFile("answers.jsonl", ANSWERS),
		);
		await user.selectOptions(
			screen.getByLabelText("Judge model"),
			"mistral-small:24b",
		);
		await expectNoAccessibilityViolations(container);
		await user.click(screen.getByRole("button", { name: "Start evaluation" }));

		await waitFor(() =>
			expect(currentPath(router)).toMatch(/^\/admin\/evaluations\/e-/),
		);
		const [sent] = api.requestsTo("POST", "/admin/evaluations");
		expect(sent?.body).toEqual({
			project_id: project.id,
			judge_model: "mistral-small:24b",
			question_file: QUESTIONS,
			other_systems: [{ name: "System B", answers_file: ANSWERS }],
		});
	});

	it("offers only the judges the API lists, with the fairness note", async () => {
		const { api } = admin();
		api.evaluations.judgeModels = ["qwen3.5:9b", "gemma3:12b"];
		renderApp("/admin/evaluations/new");

		const judge = await screen.findByLabelText("Judge model");
		await waitFor(() =>
			expect(
				within(judge)
					.getAllByRole("option")
					.map((o) => o.textContent),
			).toEqual(["qwen3.5:9b (also writes the answers)", "gemma3:12b"]),
		);
		expect(
			screen.getByText(
				"The judge should be from a different model family than the writer.",
			),
		).toBeInTheDocument();
	});

	it("shows the API's refusal of a file in its own words", async () => {
		const { project } = admin();
		const { user } = renderApp("/admin/evaluations/new");

		await screen.findByRole("option", { name: project.name });
		await user.selectOptions(screen.getByLabelText("Project"), project.id);
		await user.upload(
			screen.getByLabelText("Question file"),
			textFile("questions.txt", "\n\n"),
		);
		await user.click(screen.getByRole("button", { name: "Start evaluation" }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"The question file has no questions. Put one question on each line.",
		);
	});
});

describe("an evaluation's results", () => {
	it("gives each metric's means and difference, and wins, ties and losses", async () => {
		const { api, project } = admin();
		const evaluation = api.evaluations.add(finishedEvaluation(project.id));
		const { container } = renderApp(`/admin/evaluations/${evaluation.id}`);

		const table = await screen.findByRole("table", {
			name: "humani-t compared with System B",
		});
		const coverage = within(table).getByRole("row", { name: /Coverage/ });
		expect(coverage).toHaveTextContent("2.00");
		expect(coverage).toHaveTextContent("0.50");
		expect(coverage).toHaveTextContent("+1.50");
		expect(
			screen.getByText("humani-t won 1, tied 0 and lost 0 of 1 questions."),
		).toBeInTheDocument();
		expect(
			screen.getByText("Made by qwen3.5:9b. Judged by mistral-small:24b."),
		).toBeInTheDocument();
		await expectNoAccessibilityViolations(container);
	});

	it("shows each judgement as A and B side by side, systems hidden until revealed", async () => {
		const { api, project } = admin();
		const evaluation = api.evaluations.add(finishedEvaluation(project.id));
		const { user } = renderApp(`/admin/evaluations/${evaluation.id}`);

		const judgement = await screen.findByRole("article", {
			name: "Question 1",
		});
		const a = within(judgement).getByRole("region", { name: "Answer A" });
		const b = within(judgement).getByRole("region", { name: "Answer B" });
		expect(a).toHaveTextContent("Yes, by a tenth.");
		expect(b).toHaveTextContent("Prices rose in small markets.");
		expect(b).toHaveTextContent("Coverage: 2 of 2, good");
		expect(judgement).toHaveTextContent("The judge preferred B.");
		expect(judgement).toHaveTextContent("B names where prices rose.");
		expect(judgement).not.toHaveTextContent("humani-t");

		await user.click(
			within(judgement).getByRole("button", { name: "Reveal systems" }),
		);

		expect(a).toHaveTextContent("System B");
		expect(b).toHaveTextContent("humani-t");
	});

	it("says how far a running evaluation has got", async () => {
		const { api, project } = admin();
		const evaluation = api.evaluations.add(
			finishedEvaluation(project.id, {
				status: "running",
				question_count: 3,
				answered_count: 1,
				finished_at: null,
			}),
		);
		renderApp(`/admin/evaluations/${evaluation.id}`);

		expect(
			await screen.findByText("Answered 1 of 3 questions"),
		).toBeInTheDocument();
	});

	it("says why a failed evaluation stopped", async () => {
		const { api, project } = admin();
		const evaluation = api.evaluations.add(
			finishedEvaluation(project.id, {
				status: "failed",
				error: "The judge model isn't answering. What was judged is kept.",
			}),
		);
		renderApp(`/admin/evaluations/${evaluation.id}`);

		expect(
			await screen.findByText(
				"Stopped: The judge model isn't answering. What was judged is kept.",
			),
		).toBeInTheDocument();
	});

	it("lists humani-t's answers when no other system was compared", async () => {
		const { api, project } = admin();
		const evaluation = api.evaluations.add(
			finishedEvaluation(project.id, {
				systems: ["humani-t"],
				system_count: 0,
				results: [],
				judgements: [],
			}),
		);
		renderApp(`/admin/evaluations/${evaluation.id}`);

		expect(
			await screen.findByText(
				"No other system's answers were uploaded, so nothing was judged.",
			),
		).toBeInTheDocument();
		expect(
			screen.getByText("Prices rose in small markets."),
		).toBeInTheDocument();
	});
});
