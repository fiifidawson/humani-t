import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { FakeEventSource } from "../../test/fakeEventSource";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";

function pausedForPrivacyReview(role: "owner" | "reviewer" = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	const project = api.addProject("Cash vs vouchers, Gao", role);
	const review = api.reviews.add(project.id, "Cash or vouchers in camps?");
	api.reviews.startPlan(review, "shallow");
	api.reviews.accept(review);
	const run = api.reviews.addRun(review, "running");
	const report = api.documents.add(project.id, "field_report.txt", {
		status: "awaiting_review",
	});
	const minutes = api.documents.add(project.id, "minutes.txt", {
		status: "awaiting_review",
	});
	const gateId = api.reviews.addGate(run, "privacy_review");
	const gate = {
		gate_id: gateId,
		kind: "privacy_review",
		payload: {
			documents: [
				{
					document_id: report.id,
					masked_items: { DATE: 1, PERSON: 2 },
				},
				{ document_id: minutes.id, masked_items: {} },
			],
		},
	};
	return {
		project,
		run,
		report,
		gate,
		path: `/p/${project.id}/r/${review.id}`,
	};
}

async function showGate(runId: string, gate: unknown) {
	const source = await waitFor(() => {
		const found = FakeEventSource.instances.find((s) =>
			s.url.startsWith(`/api/v1/runs/${runId}/events`),
		);
		expect(found).toBeDefined();
		return found as FakeEventSource;
	});
	act(() => {
		source.open();
		source.emit("stage.started", 1, {
			stage: "mask",
			position: 4,
			total: 8,
			at: "2026-11-03T10:51:00Z",
		});
		source.emit("gate.required", 2, gate);
	});
	return screen.findByRole("article", { name: "Needs you · Privacy review" });
}

beforeEach(() => {
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

describe("the privacy review gate card (UI_UX_SPEC §5.3.6, §6.6)", () => {
	it("lists the documents waiting for review with how much was masked in each", async () => {
		const { path, run, gate } = pausedForPrivacyReview();
		renderApp(path);

		const card = await showGate(run.id, gate);

		const listed = await within(card).findAllByRole("listitem");
		expect(listed.map((item) => item.textContent)).toEqual([
			expect.stringContaining("D1 field_report.txt · 3 masked"),
			expect.stringContaining("D2 minutes.txt · nothing masked"),
		]);
	});

	it("links an owner to each document's masking review", async () => {
		const { path, project, run, report, gate } = pausedForPrivacyReview();
		const { user, router } = renderApp(path);
		const card = await showGate(run.id, gate);

		const link = await within(card).findByRole("link", {
			name: "Review masking D1 field_report.txt",
		});
		expect(link).toHaveAttribute(
			"href",
			`/p/${project.id}/documents?document=${report.id}`,
		);
		await user.click(link);

		await waitFor(() =>
			expect(currentPath(router)).toBe(`/p/${project.id}/documents`),
		);
		expect(
			await screen.findByRole("region", {
				name: "Privacy review: D1 field_report.txt",
			}),
		).toBeInTheDocument();
	});

	it("shows a reviewer the documents but no link, and says who can do this", async () => {
		const { path, run, gate } = pausedForPrivacyReview("reviewer");
		renderApp(path);

		const card = await showGate(run.id, gate);

		expect(
			await within(card).findByText(/D1 field_report.txt/),
		).toBeInTheDocument();
		expect(card).toHaveTextContent("Only owners can do this.");
		expect(within(card).queryByRole("link")).not.toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { path, run, gate } = pausedForPrivacyReview();
		const { container } = renderApp(path);
		const card = await showGate(run.id, gate);
		await within(card).findByRole("link", {
			name: "Review masking D1 field_report.txt",
		});

		await expectNoAccessibilityViolations(container);
	});
});
