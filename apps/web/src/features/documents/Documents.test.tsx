import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { DocumentStatus } from "../../app/documentQueries";
import { expectNoAccessibilityViolations } from "../../test/axe";
import {
	MASKING_CHANGED,
	NOT_YOUR_ROLE,
	REVIEW_NOTICE,
	UNSUPPORTED,
} from "../../test/fakeDocuments";
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

/** Select `wanted` inside `container`, as a person dragging over it would. */
function selectText(container: HTMLElement, wanted: string) {
	const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
	for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
		const at = node.textContent?.indexOf(wanted) ?? -1;
		if (at >= 0) {
			const range = document.createRange();
			range.setStart(node, at);
			range.setEnd(node, at + wanted.length);
			window.getSelection()?.removeAllRanges();
			window.getSelection()?.addRange(range);
			fireEvent.mouseUp(node.parentElement ?? container);
			return;
		}
	}
	throw new Error(`No text "${wanted}" to select`);
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

	it("shows the owner the original beside the masked text, and says the view is recorded", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		const original = await screen.findByRole("region", {
			name: "Original text",
		});
		expect(original).toHaveTextContent("Kofi Mensah leads the team.");
		const masked = screen.getByRole("region", {
			name: "What the model will see",
		});
		expect(within(masked).getByText("masked person 1")).toBeInTheDocument();
		expect(masked).not.toHaveTextContent("Kofi Mensah");
		expect(screen.getByText(REVIEW_NOTICE)).toBeInTheDocument();
		expect(
			api.requestsTo(
				"GET",
				`/projects/${project.id}/documents/${waiting.id}/review`,
			),
		).toHaveLength(1);
	});

	it("lists every mask with what it hides, and offers Not personal only where the API allows it", async () => {
		const { project } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		const masks = await screen.findByRole("list", { name: "Masks" });
		const items = within(masks).getAllByRole("listitem");
		expect(items.map((item) => item.textContent)).toEqual([
			expect.stringContaining("“12 March” · Date · Found automatically"),
			expect.stringContaining("“amina@example.org” · Email address"),
			expect.stringContaining("“Kofi Mensah” · Person"),
			expect.stringContaining("“448-1207” · Medical record number"),
		]);
		expect(
			within(masks).getByRole("button", { name: "Not personal: 12 March" }),
		).toBeInTheDocument();
		expect(
			within(masks).queryByRole("button", {
				name: "Not personal: amina@example.org",
			}),
		).not.toBeInTheDocument();
	});

	it("removes a mask that is not personal, with the reason the owner gives", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		await user.click(
			await screen.findByRole("button", { name: "Not personal: 12 March" }),
		);
		const remove = screen.getByRole("button", { name: "Remove mask" });
		expect(remove).toBeDisabled();
		await user.type(
			screen.getByRole("textbox", { name: "Why it isn't personal" }),
			"Date of a public meeting",
		);
		await user.click(remove);

		expect(
			await screen.findByText("“12 March” is no longer masked."),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/unmask`,
			)[0]?.body,
		).toEqual({
			version: 1,
			start: 13,
			end: 21,
			reason: "Date of a public meeting",
		});
		expect(
			screen.getByRole("region", { name: "What the model will see" }),
		).toHaveTextContent("Interview on 12 March with");
	});

	it("masks text the detectors missed, from a selection in the original", async () => {
		const { api, project } = signedInWith("owner");
		const waiting = api.documents.add(project.id, "report.txt", {
			status: "awaiting_review",
			original: {
				text: "Dr Traoré met the team at the clinic.",
				found: [],
			},
		});
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		selectText(
			await screen.findByRole("region", { name: "Original text" }),
			"Dr Traoré",
		);
		expect(
			await screen.findByText("Selected: “Dr Traoré”"),
		).toBeInTheDocument();
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Mask as" }),
			"Person",
		);
		await user.click(screen.getByRole("button", { name: "Mask it" }));

		expect(
			await screen.findByText("“Dr Traoré” is masked."),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/masks`,
			)[0]?.body,
		).toEqual({ version: 1, start: 0, end: 9, entity_type: "PERSON" });
		expect(
			within(
				screen.getByRole("region", { name: "What the model will see" }),
			).getByText("masked person 1"),
		).toBeInTheDocument();
	});

	it("makes a masked name a project rule, and masks it everywhere in the document now", async () => {
		const { api, project } = signedInWith("owner");
		const waiting = api.documents.add(project.id, "report.txt", {
			status: "awaiting_review",
			original: {
				text: "Kofi Mensah met the team. Later, kofi mensah left.",
				found: [{ value: "Kofi Mensah", type: "PERSON", source: "names@1" }],
			},
		});
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);

		await user.click(
			await screen.findByRole("button", {
				name: "Always mask in this project: Kofi Mensah",
			}),
		);

		expect(
			await screen.findByText(
				"“Kofi Mensah” is now masked everywhere in this project.",
			),
		).toBeInTheDocument();
		expect(
			api.requestsTo("POST", `/projects/${project.id}/masking-rules`)[0]?.body,
		).toEqual({ term: "Kofi Mensah", entity_type: "PERSON" });
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/masks`,
			),
		).toHaveLength(1);
		expect(
			screen.getByRole("region", { name: "What the model will see" }),
		).not.toHaveTextContent(/kofi mensah/i);
	});

	it("approves the masked version on screen after a correction", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await user.click(
			await screen.findByRole("button", { name: "Not personal: 12 March" }),
		);
		await user.type(
			screen.getByRole("textbox", { name: "Why it isn't personal" }),
			"A public meeting",
		);
		await user.click(screen.getByRole("button", { name: "Remove mask" }));
		await screen.findByText("“12 March” is no longer masked.");

		await user.click(screen.getByRole("button", { name: "Approve" }));

		expect(
			await screen.findByText("Approved. The brief can now cite it as D1."),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"POST",
				`/projects/${project.id}/documents/${waiting.id}/approve`,
			)[0]?.body,
		).toEqual({ version: 2 });
	});

	it("shows the API's answer when a correction meets a newer masking", async () => {
		const { api, project, waiting } = withDocumentWaiting();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", { name: "Review D1 report.txt" }),
		);
		await user.click(
			await screen.findByRole("button", { name: "Not personal: 12 March" }),
		);
		await user.type(
			screen.getByRole("textbox", { name: "Why it isn't personal" }),
			"A public meeting",
		);

		api.documents.correct(waiting.id);
		await user.click(screen.getByRole("button", { name: "Remove mask" }));

		expect(await screen.findByText(MASKING_CHANGED)).toBeInTheDocument();
	});

	it("never asks for the original when an owner reads an approved document", async () => {
		const { api, project } = signedInWith("owner");
		api.documents.add(project.id, "report.txt", { status: "approved" });
		const { user } = await openDocuments(project.id);

		await user.click(
			await screen.findByRole("button", {
				name: "See masked text D1 report.txt",
			}),
		);

		await screen.findByRole("region", {
			name: "What the model will see: D1 report.txt",
		});
		expect(await screen.findByText("masked person 1")).toBeInTheDocument();
		expect(api.requests.filter((r) => r.path.endsWith("/review"))).toEqual([]);
	});

	it.each<Role>(["reviewer", "viewer"])(
		"lets a %s see what the model will see, never the original, without approving",
		async (role) => {
			const { api, project } = withDocumentWaiting(role);
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
			expect(view).not.toHaveTextContent("Kofi Mensah");
			expect(api.requests.filter((r) => r.path.endsWith("/review"))).toEqual(
				[],
			);
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

describe("deleting a document (UI_UX_SPEC §6.13, §3.3)", () => {
	function withDocument(role: Role = "owner") {
		const { api, project } = signedInWith(role);
		const report = api.documents.add(project.id, "report.txt", {
			status: "approved",
		});
		return { api, project, report };
	}

	async function openDeleteDialog(projectId: string) {
		const view = await openDocuments(projectId);
		await view.user.click(
			await screen.findByRole("button", { name: "Delete D1 report.txt" }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Delete D1 report.txt?",
		});
		return { ...view, dialog };
	}

	it("asks the owner to confirm, and says a brief may already cite it", async () => {
		const { project } = withDocument();
		const { dialog } = await openDeleteDialog(project.id);

		expect(dialog).toHaveTextContent(
			"This deletes the file and its text. A brief that already cites D1 keeps the passages it quoted. This can't be undone.",
		);
	});

	it("deletes the document once confirmed, and takes it off the list", async () => {
		const { api, project, report } = withDocument();
		const { user, dialog } = await openDeleteDialog(project.id);

		await user.click(within(dialog).getByRole("button", { name: "Delete" }));

		expect(
			await screen.findByText("D1 report.txt is deleted."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("cell", { name: "report.txt" }),
		).not.toBeInTheDocument();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		const [deleted] = api.requestsTo(
			"DELETE",
			`/projects/${project.id}/documents/${report.id}`,
		);
		expect(deleted?.csrfToken).toBe(api.csrfToken);
		expect(api.documents.all).toEqual([]);
	});

	it("keeps the document when the owner cancels", async () => {
		const { api, project, report } = withDocument();
		const { user, dialog } = await openDeleteDialog(project.id);

		await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(
			screen.getByRole("cell", { name: "report.txt" }),
		).toBeInTheDocument();
		expect(
			api.requestsTo(
				"DELETE",
				`/projects/${project.id}/documents/${report.id}`,
			),
		).toEqual([]);
	});

	it.each<DocumentStatus>([
		"uploaded",
		"awaiting_review",
		"rejected",
		"failed",
	])("lets the owner delete a document that is %s", async (status) => {
		const { api, project } = signedInWith("owner");
		api.documents.add(project.id, "report.txt", {
			status,
			...(status === "failed" ? { failure: "interrupted" as const } : {}),
		});
		await openDocuments(project.id);

		expect(
			await screen.findByRole("button", { name: "Delete D1 report.txt" }),
		).toBeInTheDocument();
	});

	it("closes the masked text of the document it deletes", async () => {
		const { project } = withDocument();
		const { user } = await openDocuments(project.id);
		await user.click(
			await screen.findByRole("button", {
				name: "See masked text D1 report.txt",
			}),
		);
		await screen.findByRole("region", {
			name: "What the model will see: D1 report.txt",
		});

		await user.click(
			screen.getByRole("button", { name: "Delete D1 report.txt" }),
		);
		await user.click(
			within(
				await screen.findByRole("dialog", { name: "Delete D1 report.txt?" }),
			).getByRole("button", { name: "Delete" }),
		);

		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: /What the model will see/ }),
			).not.toBeInTheDocument(),
		);
	});

	it("says so when another owner already deleted it", async () => {
		const { api, project, report } = withDocument();
		const { user, dialog } = await openDeleteDialog(project.id);

		api.documents.remove(report.id);
		await user.click(within(dialog).getByRole("button", { name: "Delete" }));

		expect(
			await screen.findByText("D1 report.txt was already deleted."),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(
				screen.queryByRole("cell", { name: "report.txt" }),
			).not.toBeInTheDocument(),
		);
	});

	it("shows the API's refusal and keeps the dialog open when the owner is no longer one", async () => {
		const { api, project } = withDocument();
		const { user, dialog } = await openDeleteDialog(project.id);

		project.role = "reviewer";
		await user.click(within(dialog).getByRole("button", { name: "Delete" }));

		expect(await within(dialog).findByRole("alert")).toHaveTextContent(
			NOT_YOUR_ROLE,
		);
		expect(api.documents.all).toHaveLength(1);
	});

	it.each<Role>(["reviewer", "viewer"])(
		"offers a %s no way to delete a document",
		async (role) => {
			const { project } = withDocument(role);
			await openDocuments(project.id);
			await rowFor("report.txt");

			expect(
				screen.queryByRole("button", { name: /^Delete/ }),
			).not.toBeInTheDocument();
		},
	);

	it("has no accessibility violations with the delete dialog open", async () => {
		const { project } = withDocument();
		await openDeleteDialog(project.id);

		await expectNoAccessibilityViolations();
	});
});
