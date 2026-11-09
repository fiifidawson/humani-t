import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { MASKING_CHANGED, UNSUPPORTED } from "../../test/fakeDocuments";
import { installNodeFiles } from "../../test/nodeFiles";
import { currentPath, renderApp } from "../../test/render";
import { fakeApi } from "../../test/server";
import { POLL_INTERVAL_MS } from "./Documents";

type Role = "owner" | "reviewer" | "viewer";

beforeEach(installNodeFiles);

function signedInWith(role: Role = "owner") {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return { api, project: api.addProject("Cash vs vouchers, Gao", role) };
}

const textFile = (name: string, content = "Interview notes from Gao.") =>
	new File([content], name, { type: "text/plain" });

async function openDocuments(projectId: string) {
	const view = renderApp(`/p/${projectId}/documents`);
	await screen.findByRole("heading", { level: 2, name: "Documents" });
	return view;
}

/** The table row of the document with this exact name. */
const rowFor = async (name: string) =>
	(await screen.findByRole("cell", { name })).closest("tr");

describe("the documents page (UI_UX_SPEC §6.13)", () => {
	it("opens from the Documents link in the project's header", async () => {
		const { project } = signedInWith("owner");
		const { user, router } = renderApp(`/p/${project.id}`);

		await user.click(await screen.findByRole("link", { name: "Documents" }));

		expect(
			await screen.findByRole("heading", { level: 2, name: "Documents" }),
		).toBeInTheDocument();
		expect(currentPath(router)).toBe(`/p/${project.id}/documents`);
	});

	it("says each document's status in plain words", async () => {
		const { api, project } = signedInWith("owner");
		const documents = api.documents;
		documents.add(project.id, "queued.txt", { status: "uploaded" });
		documents.add(project.id, "scanning.txt", { status: "scanning" });
		documents.add(project.id, "reading.pdf", { status: "extracting" });
		documents.add(project.id, "masking.docx", { status: "masking" });
		documents.add(project.id, "review.txt", { status: "awaiting_review" });
		documents.add(project.id, "ready.md", { status: "approved" });
		documents.add(project.id, "rejected.txt", { status: "rejected" });
		documents.add(project.id, "scan.pdf", {
			status: "failed",
			failure: "no_text",
		});
		await openDocuments(project.id);

		const statusOf = async (name: string) =>
			within((await rowFor(name)) ?? document.body).getByTestId("status")
				.textContent;
		expect(await statusOf("queued.txt")).toBe("Checking for viruses");
		expect(await statusOf("scanning.txt")).toBe("Checking for viruses");
		expect(await statusOf("reading.pdf")).toBe("Reading the text");
		expect(await statusOf("masking.docx")).toBe("Reading the text");
		expect(await statusOf("review.txt")).toBe("Waiting for privacy review");
		expect(await statusOf("ready.md")).toBe("Ready");
		expect(await statusOf("rejected.txt")).toBe("Rejected");
		expect(await statusOf("scan.pdf")).toBe("Failed");
		expect(
			screen.getByText(
				"No text was found. A scanned document needs text recognition (OCR) first.",
			),
		).toBeInTheDocument();
	});

	it("shows each document's tag, name, size and who uploaded it", async () => {
		const { api, project } = signedInWith("owner");
		const kofi = api.addUser({
			email: "kofi@example.org",
			displayName: "Kofi M.",
		});
		api.addMember(project, kofi, "owner");
		api.documents.add(project.id, "minutes.txt", { sizeBytes: 2048 });
		api.documents.add(project.id, "field_report.pdf", {
			uploadedBy: kofi.id,
			sizeBytes: 3.5 * 1024 * 1024,
		});
		await openDocuments(project.id);

		const mine = within((await rowFor("minutes.txt")) ?? document.body);
		expect(mine.getByText("D1")).toBeInTheDocument();
		expect(mine.getByText("2 KB")).toBeInTheDocument();
		expect(mine.getByText("You")).toBeInTheDocument();
		const theirs = within((await rowFor("field_report.pdf")) ?? document.body);
		expect(theirs.getByText("D2")).toBeInTheDocument();
		expect(theirs.getByText("3.5 MB")).toBeInTheDocument();
		expect(await theirs.findByText("Kofi M.")).toBeInTheDocument();
	});

	it("says so when the project has no documents yet", async () => {
		const { project } = signedInWith("viewer");
		await openDocuments(project.id);

		expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
	});

	it("has no accessibility violations", async () => {
		const { api, project } = signedInWith("owner");
		api.documents.add(project.id, "review.txt", { status: "awaiting_review" });
		api.documents.add(project.id, "scan.pdf", {
			status: "failed",
			failure: "encrypted",
		});
		const { container } = await openDocuments(project.id);
		await rowFor("review.txt");

		await expectNoAccessibilityViolations(container);
	});
});

describe("uploading a document (UI_UX_SPEC §6.13, §3.3)", () => {
	it("sends a chosen file as multipart with the CSRF token, and lists it at once", async () => {
		const { api, project } = signedInWith("owner");
		const { user } = await openDocuments(project.id);

		await user.upload(
			await screen.findByLabelText("Choose a file"),
			textFile("report.txt"),
		);

		const row = within((await rowFor("report.txt")) ?? document.body);
		expect(row.getByTestId("status")).toHaveTextContent("Checking for viruses");
		const [upload] = api.requestsTo(
			"POST",
			`/projects/${project.id}/documents`,
		);
		expect(upload?.contentType).toMatch(/^multipart\/form-data/);
		expect(upload?.csrfToken).toBe(api.csrfToken);
	});

	it("accepts a file dropped on the upload area", async () => {
		const { project } = signedInWith("owner");
		await openDocuments(project.id);

		fireEvent.drop(await screen.findByTestId("drop-zone"), {
			dataTransfer: { files: [textFile("dropped.md", "# Notes")] },
		});

		expect(await rowFor("dropped.md")).not.toBeNull();
	});

	it("tells the person which files it takes and how large", async () => {
		const { project } = signedInWith("owner");
		await openDocuments(project.id);

		expect(
			await screen.findByText(
				"PDF, Word (.docx), plain text or Markdown, up to 20 MB.",
			),
		).toBeInTheDocument();
		expect(screen.getByLabelText("Choose a file")).toHaveAttribute(
			"accept",
			".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown",
		);
	});

	it("shows the API's refusal in its own words", async () => {
		const { api, project } = signedInWith("owner");
		const { user } = await openDocuments(project.id);

		await user.upload(
			await screen.findByLabelText("Choose a file"),
			new File([new Uint8Array([0x4d, 0x5a, 0x00, 0x01])], "notes.txt"),
		);

		expect(await screen.findByText(UNSUPPORTED)).toBeInTheDocument();
		expect(api.documents.all).toEqual([]);
	});

	it("refuses a file larger than the deployment takes without sending it", async () => {
		const { api, project } = signedInWith("owner");
		api.documents.maxFileBytes = 1024 * 1024;
		const { user } = await openDocuments(project.id);

		await user.upload(
			await screen.findByLabelText("Choose a file"),
			textFile("big.txt", "a".repeat(1024 * 1024 + 1)),
		);

		expect(
			await screen.findByText(
				"big.txt is over 1 MB, the largest file allowed.",
			),
		).toBeInTheDocument();
		expect(api.requestsTo("POST", `/projects/${project.id}/documents`)).toEqual(
			[],
		);
	});

	it("says why uploads are off when the operator has switched them off", async () => {
		const { api, project } = signedInWith("owner");
		api.documents.disabledReason =
			"Uploads are off until the operator chooses a malware scanner.";
		await openDocuments(project.id);

		expect(
			await screen.findByText(
				"Uploads are off until the operator chooses a malware scanner.",
			),
		).toBeInTheDocument();
		expect(screen.queryByLabelText("Choose a file")).not.toBeInTheDocument();
	});

	it.each<Role>(["reviewer", "viewer"])(
		"tells a %s that only owners can upload, and offers no upload",
		async (role) => {
			const { project } = signedInWith(role);
			await openDocuments(project.id);

			expect(
				await screen.findByText("Only owners can upload documents."),
			).toBeInTheDocument();
			expect(screen.queryByLabelText("Choose a file")).not.toBeInTheDocument();
		},
	);
});

describe("following a document while it is processed", () => {
	it("looks again while a document is in progress, until it is ready for review", async () => {
		const { api, project } = signedInWith("owner");
		const queued = api.documents.add(project.id, "report.txt");
		await openDocuments(project.id);
		await rowFor("report.txt");

		api.documents.finishProcessing(queued.id);

		const row = within((await rowFor("report.txt")) ?? document.body);
		await waitFor(() =>
			expect(row.getByTestId("status")).toHaveTextContent(
				"Waiting for privacy review",
			),
		);
	});

	it("stops looking once nothing is in progress", async () => {
		const { api, project } = signedInWith("owner");
		api.documents.add(project.id, "ready.txt", { status: "approved" });
		await openDocuments(project.id);
		await rowFor("ready.txt");

		await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS + 500));

		expect(
			api.requestsTo("GET", `/projects/${project.id}/documents`),
		).toHaveLength(1);
	});
});

describe("the owner's privacy review (UI_UX_SPEC §6.6, §6.13)", () => {
	function withDocumentWaiting(role: Role = "owner") {
		const { api, project } = signedInWith(role);
		const waiting = api.documents.add(project.id, "report.txt", {
			status: "awaiting_review",
		});
		return { api, project, waiting };
	}

	it("shows the masked text and what was masked, by type", async () => {
		const { project } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);

		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		const review = await screen.findByRole("region", {
			name: "Privacy review: D1 report.txt",
		});
		expect(within(review).getByText("masked date 1")).toBeInTheDocument();
		expect(
			within(review).getByText("masked medical record 1"),
		).toBeInTheDocument();
		expect(
			within(review).getByText(
				"Masked: 1 date, 1 email address, 1 person and 1 medical record number.",
			),
		).toBeInTheDocument();
		expect(
			within(review).getByText(
				"If you approve it, this masked text becomes evidence the brief can cite as D1. If you reject it, it is never used.",
			),
		).toBeInTheDocument();
	});

	it("approves the masked version the owner saw, and the document becomes ready", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		await user.click(await screen.findByRole("button", { name: "Approve" }));

		expect(
			await screen.findByText("Approved. The brief can now cite it as D1."),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/approve`,
			)[0]?.body,
		).toEqual({ version: 1 });
		const row = within((await rowFor("report.txt")) ?? document.body);
		await waitFor(() =>
			expect(row.getByTestId("status")).toHaveTextContent("Ready"),
		);
	});

	it("rejects a document so it is never used", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		await user.click(await screen.findByRole("button", { name: "Reject" }));

		expect(
			await screen.findByText("Rejected. It won't be used."),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/reject`,
			)[0]?.body,
		).toEqual({ version: 1 });
	});

	it("shows the API's answer when the masking changed after it was opened", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await screen.findByRole("button", { name: "Approve" });

		api.documents.correct(waiting.id);
		await user.click(screen.getByRole("button", { name: "Approve" }));

		expect(await screen.findByText(MASKING_CHANGED)).toBeInTheDocument();
		expect(waiting.status).toBe("awaiting_review");
	});

	it("closes the review", async () => {
		const { project } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await screen.findByRole("region", {
			name: "Privacy review: D1 report.txt",
		});

		await user.click(screen.getByRole("button", { name: "Close" }));

		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: /Privacy review/ }),
			).not.toBeInTheDocument(),
		);
	});

	it("never asks for or shows the original text", async () => {
		const { api, project } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await screen.findByRole("button", { name: "Approve" });

		expect(api.requests.filter((r) => r.path.endsWith("/review"))).toEqual([]);
	});

	it.each<Role>(["reviewer", "viewer"])(
		"lets a %s see what the model will see, without approving",
		async (role) => {
			const { project } = withDocumentWaiting(role);
			const { user } = await openDocuments(project.id);

			await user.click(
				await screen.findByRole("button", {
					name: "See masked text D1 report.txt",
				}),
			);

			const view = await screen.findByRole("region", {
				name: "What the model will see: D1 report.txt",
			});
			expect(within(view).getByText("masked date 1")).toBeInTheDocument();
			expect(
				within(view).queryByRole("button", { name: "Approve" }),
			).not.toBeInTheDocument();
			expect(
				within(view).getByText("Only owners can approve or reject documents."),
			).toBeInTheDocument();
		},
	);

	it("has no accessibility violations with the review open", async () => {
		const { project } = withDocumentWaiting();
		const { user, container } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await screen.findByRole("button", { name: "Approve" });

		await expectNoAccessibilityViolations(container);
	});
});
