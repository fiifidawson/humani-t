/**
 * The documents half of the fake API: upload, the listing, masked text, the
 * owner's approve or reject, and delete, served through MSW like the rest of
 * fakeApi.ts.
 *
 * It applies the API's rules the screens depend on: a non-member gets 404, only
 * owners upload, decide and delete, the upload is multipart with exactly one
 * `file` part, its type is read from the bytes and never from the name, size
 * and quota are checked before anything is kept, masked text is there only
 * once a document is masked, a decision names the masked version it was
 * about, and a deleted document's tag is never given to another.
 * Processing is driven by the test (`finishProcessing`, `fail`), as the API's
 * worker would drive it, so a screen has to look again to see it.
 *
 * Shapes and messages follow docs/api.md "Documents" and
 * services/api/app/domain/documents.py. Raw text is never held here: the
 * screens never ask for it.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, nextId, problem } from "./fakeHttp";

const API = "*/api/v1";
const MIB = 1024 * 1024;

type Schemas = components["schemas"];
type Role = Schemas["Role"];
export type FakeDocument = Schemas["DocumentOut"];
type Status = Schemas["DocumentStatus"];
type Failure = Schemas["DocumentFailure"];

export const UNSUPPORTED =
	"This kind of file isn't accepted. Upload a PDF, a Word document (.docx), or plain text or Markdown in UTF-8.";
export const MASKING_CHANGED =
	"The masking changed since you looked at it. Reload it and review again.";
export const NOT_YOUR_ROLE = "Your role in this project does not allow this.";

/** The masked text a document gets when the test does not say. */
export const EXAMPLE_MASKED_TEXT =
	"Interview on [DATE_1] with [EMAIL_1]. [PERSON_1] leads the team. MRN [MEDICAL_RECORD_1] was referred.";

const FAILURE_MESSAGES: Readonly<Record<Failure, string>> = {
	malware_found:
		"The malware scanner flagged this file, so it was deleted unread.",
	scan_failed:
		"The malware scanner could not check this file, so it was not read.",
	unreadable: "The file could not be read. It may be damaged.",
	encrypted:
		"The file is password-protected. Remove the password and upload it again.",
	no_text:
		"No text was found. A scanned document needs text recognition (OCR) first.",
	too_complex: "The file is too large or too deeply packed to read safely.",
	refused_content:
		"The file contains structures that are refused for safety (such as external references).",
	timed_out: "Reading the file took too long, so it was stopped.",
	interrupted:
		"Processing was interrupted several times. Delete the file and upload it again.",
};

const STATE_MESSAGES: Partial<Record<Status, string>> = {
	uploaded: "This document is still waiting to be read.",
	scanning: "This document is still being checked.",
	extracting: "This document is still being read.",
	masking: "This document is still being masked.",
	approved: "This document's masking is already approved.",
	rejected:
		"This document was rejected. Delete it and upload a corrected file.",
	failed: "This document could not be processed.",
};

const PLACEHOLDER = /\[([A-Z]+(?:_[A-Z]+)*)_(\d+)\]/g;

const ENTITY_TYPES: readonly Schemas["EntityType"][] = [
	"PERSON",
	"LOCATION",
	"DATE",
	"EMAIL",
	"PHONE",
	"MEDICAL_RECORD",
	"ID_NUMBER",
	"PASSPORT",
	"IBAN",
	"CARD_NUMBER",
	"IP_ADDRESS",
	"COORDINATES",
];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	roleIn: (projectId: string) => Role | null;
	userId: () => string | null;
}

interface Stored {
	projectId: string;
	document: FakeDocument;
	maskedText: string | null;
}

export interface DocumentOptions {
	status?: Status;
	uploadedBy?: string;
	sizeBytes?: number;
	fileType?: Schemas["DocumentType"];
	maskedText?: string;
	failure?: Failure;
	isTruncated?: boolean;
}

/** Distinct placeholders by type, as the API counts `masked_items`. */
function countMasked(maskedText: string): Record<string, number> {
	const seen = new Map<string, Set<string>>();
	for (const [whole, type = ""] of maskedText.matchAll(PLACEHOLDER)) {
		seen.set(type, (seen.get(type) ?? new Set()).add(whole));
	}
	return Object.fromEntries(
		[...seen].map(([type, values]) => [type, values.size]),
	);
}

/** The API's rule (domain/documents.py `detect_document_type`), for the bytes a test sends. */
function detectType(
	bytes: Uint8Array,
	filename: string,
): Schemas["DocumentType"] | null {
	const head = String.fromCharCode(...bytes.slice(0, 5));
	if (head === "%PDF-") {
		return "pdf";
	}
	if (head.startsWith("PK")) {
		return "docx";
	}
	try {
		const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
		// biome-ignore lint/suspicious/noControlCharactersInRegex: control characters are what marks a binary file
		if (/[\u0000-\u0008\u000e-\u001f]/.test(text)) {
			return null;
		}
	} catch {
		return null;
	}
	return /\.(md|markdown)\s*$/i.test(filename) ? "md" : "txt";
}

export class FakeDocuments {
	readonly all: Stored[] = [];
	maxFileBytes = 20 * MIB;
	maxDocuments = 200;
	maxSizeBytes = 1024 * MIB;
	/** When set, uploads are off and this is why, as when the operator chose no scanner. */
	disabledReason: string | null = null;
	private readonly highestNumber = new Map<string, number>();

	constructor(private readonly access: Access) {}

	/** Arrange a document as if it had been uploaded and processed this far. */
	add(
		projectId: string,
		name: string,
		options: DocumentOptions = {},
	): FakeDocument {
		const number = (this.highestNumber.get(projectId) ?? 0) + 1;
		this.highestNumber.set(projectId, number);
		const status = options.status ?? "uploaded";
		const isMasked = ["awaiting_review", "approved", "rejected"].includes(
			status,
		);
		const maskedText = isMasked
			? (options.maskedText ?? EXAMPLE_MASKED_TEXT)
			: null;
		const document: FakeDocument = {
			id: nextId("d"),
			tag: `D${number}`,
			name,
			file_type: options.fileType ?? "txt",
			size_bytes: options.sizeBytes ?? 2048,
			status,
			failure: options.failure ?? null,
			failure_message:
				options.failure === undefined
					? null
					: FAILURE_MESSAGES[options.failure],
			uploaded_by: options.uploadedBy ?? this.access.userId() ?? nextId("u"),
			uploaded_at: "2026-11-05T09:00:00Z",
			scanned_with: status === "uploaded" ? null : "none",
			extracted_with: null,
			page_count: null,
			character_count: maskedText?.length ?? null,
			is_truncated: options.isTruncated ?? false,
			masked_items: maskedText === null ? {} : countMasked(maskedText),
			mask_version: maskedText === null ? 0 : 1,
			last_review: null,
		};
		this.all.push({ projectId, document, maskedText });
		return document;
	}

	/** The worker's part: scanned, read and masked, now waiting for an owner. */
	finishProcessing(documentId: string, maskedText = EXAMPLE_MASKED_TEXT): void {
		const stored = this.find(documentId);
		stored.maskedText = maskedText;
		Object.assign(stored.document, {
			status: "awaiting_review",
			scanned_with: "none",
			character_count: maskedText.length,
			masked_items: countMasked(maskedText),
			mask_version: 1,
		} satisfies Partial<FakeDocument>);
	}

	fail(documentId: string, failure: Failure): void {
		Object.assign(this.find(documentId).document, {
			status: "failed",
			failure,
			failure_message: FAILURE_MESSAGES[failure],
		} satisfies Partial<FakeDocument>);
	}

	/** Another owner corrected the masking: a new version, back to review. */
	correct(documentId: string): void {
		const { document } = this.find(documentId);
		document.mask_version += 1;
		document.status = "awaiting_review";
	}

	/** Another owner deleted it. Its tag stays used, as the API keeps it. */
	remove(documentId: string): void {
		this.all.splice(this.all.indexOf(this.find(documentId)), 1);
	}

	private find(documentId: string): Stored {
		const stored = this.all.find((s) => s.document.id === documentId);
		if (stored === undefined) {
			throw new Error(`No fake document ${documentId}`);
		}
		return stored;
	}

	private inProject(projectId: string): Stored[] {
		return this.all
			.filter((s) => s.projectId === projectId)
			.sort(
				(a, b) =>
					Number(a.document.tag.slice(1)) - Number(b.document.tag.slice(1)),
			);
	}

	/** 401 or 404 as the API's gate answers, or the caller's role. */
	private gate(projectId: string): Response | Role {
		const refused = this.access.refuseUnlessSignedIn();
		if (refused !== null) {
			return refused;
		}
		return this.access.roleIn(projectId) ?? problem(404, "Not Found");
	}

	private storedIn(projectId: string, documentId: string): Stored | undefined {
		return this.all.find(
			(s) => s.projectId === projectId && s.document.id === documentId,
		);
	}

	private async upload(projectId: string, request: Request) {
		if (this.disabledReason !== null) {
			return problem(503, this.disabledReason);
		}
		const contentType = request.headers.get("content-type") ?? "";
		if (!contentType.startsWith("multipart/form-data")) {
			return problem(415, "Send the file as multipart/form-data.");
		}
		const form = await request.formData();
		const files = form.getAll("file");
		const file = files[0];
		if (files.length !== 1 || typeof file === "string" || file === undefined) {
			return problem(422, "Send exactly one file per upload.");
		}
		if (file.size === 0) {
			return problem(422, "The file is empty.");
		}
		if (file.size > this.maxFileBytes) {
			return problem(
				413,
				`The file is larger than this deployment accepts (${Math.floor(this.maxFileBytes / MIB)} MiB).`,
			);
		}
		const fileType = detectType(
			new Uint8Array(await file.arrayBuffer()),
			file.name,
		);
		if (fileType === null) {
			return problem(415, UNSUPPORTED);
		}
		const usage = this.inProject(projectId);
		if (usage.length >= this.maxDocuments) {
			return problem(
				409,
				`This project already has ${usage.length} documents, the most it can hold. Delete one before uploading another.`,
			);
		}
		const document = this.add(projectId, file.name, {
			status: "uploaded",
			sizeBytes: file.size,
			fileType,
		});
		return HttpResponse.json(document, {
			status: 202,
			headers: {
				location: `/api/v1/projects/${projectId}/documents/${document.id}`,
			},
		});
	}

	/** No recent sign-in needed: the API asks only for the owner's role. */
	private delete(projectId: string, documentId: string) {
		const role = this.gate(projectId);
		if (role instanceof Response) {
			return role;
		}
		if (role !== "owner") {
			return problem(403, NOT_YOUR_ROLE);
		}
		const stored = this.storedIn(projectId, documentId);
		if (stored === undefined) {
			return problem(404, "Not Found");
		}
		this.remove(documentId);
		return new HttpResponse(null, { status: 204 });
	}

	private async decide(
		projectId: string,
		documentId: string,
		decision: Schemas["DocumentDecision"],
		request: Request,
	) {
		const role = this.gate(projectId);
		if (role instanceof Response) {
			return role;
		}
		if (role !== "owner") {
			return problem(403, NOT_YOUR_ROLE);
		}
		const stored = this.storedIn(projectId, documentId);
		if (stored === undefined) {
			return problem(404, "Not Found");
		}
		const { document } = stored;
		if (document.status !== "awaiting_review") {
			return problem(
				409,
				STATE_MESSAGES[document.status] ?? "This document can't be reviewed.",
			);
		}
		if ((await jsonBody(request))["version"] !== document.mask_version) {
			return problem(409, MASKING_CHANGED);
		}
		document.status = decision === "approve" ? "approved" : "rejected";
		document.last_review = {
			decision,
			mask_version: document.mask_version,
			decided_by: this.access.userId() ?? "",
			decided_at: "2026-11-05T10:00:00Z",
		};
		return HttpResponse.json(document);
	}

	handlers() {
		const documents = `${API}/projects/:projectId/documents`;
		const one = `${documents}/:documentId`;
		return [
			http.get(documents, ({ params }) => {
				const projectId = String(params["projectId"]);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				const listed = this.inProject(projectId).map((s) => s.document);
				return HttpResponse.json({
					documents: listed,
					usage: {
						documents: listed.length,
						size_bytes: listed.reduce((sum, d) => sum + d.size_bytes, 0),
						max_documents: this.maxDocuments,
						max_size_bytes: this.maxSizeBytes,
						max_file_bytes: this.maxFileBytes,
					},
					accepted_types: ["pdf", "docx", "txt", "md"],
					uploads_enabled: this.disabledReason === null,
					uploads_disabled_reason: this.disabledReason,
				} satisfies Schemas["DocumentListOut"]);
			}),

			http.post(documents, async ({ params, request }) => {
				const projectId = String(params["projectId"]);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				return role === "owner"
					? this.upload(projectId, request)
					: problem(403, NOT_YOUR_ROLE);
			}),

			http.get(one, ({ params }) => {
				const projectId = String(params["projectId"]);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				const stored = this.storedIn(projectId, String(params["documentId"]));
				return stored === undefined
					? problem(404, "Not Found")
					: HttpResponse.json(stored.document);
			}),

			http.delete(one, ({ params }) =>
				this.delete(String(params["projectId"]), String(params["documentId"])),
			),

			http.get(`${one}/masked`, ({ params }) => {
				const projectId = String(params["projectId"]);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				const stored = this.storedIn(projectId, String(params["documentId"]));
				if (stored === undefined || stored.maskedText === null) {
					return problem(404, "Not Found");
				}
				const { document, maskedText } = stored;
				return HttpResponse.json({
					document_id: document.id,
					tag: document.tag,
					status: document.status,
					mask_version: document.mask_version,
					masked_text: maskedText,
					items: [...maskedText.matchAll(PLACEHOLDER)].flatMap((match) => {
						const type = ENTITY_TYPES.find((t) => t === match[1]);
						return type === undefined
							? []
							: [
									{
										start: match.index,
										end: match.index + match[0].length,
										placeholder: match[0],
										entity_type: type,
										sources: ["patterns@1"],
									},
								];
					}),
					page_starts: [0],
					is_truncated: document.is_truncated,
				} satisfies Schemas["MaskedDocumentOut"]);
			}),

			http.post(`${one}/approve`, ({ params, request }) =>
				this.decide(
					String(params["projectId"]),
					String(params["documentId"]),
					"approve",
					request,
				),
			),

			http.post(`${one}/reject`, ({ params, request }) =>
				this.decide(
					String(params["projectId"]),
					String(params["documentId"]),
					"reject",
					request,
				),
			),
		];
	}
}
