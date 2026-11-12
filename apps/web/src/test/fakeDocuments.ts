/**
 * The documents half of the fake API, served through MSW like fakeApi.ts, with
 * the project's masking rules.
 *
 * It applies the rules the screens depend on: 404 for non-members, owner-only
 * writes and original text, the type read from the bytes, size and quota checked
 * first, tags never reused, and corrections that mask again with the project's
 * rules. Tests drive processing (`finishProcessing`, `fail`). The original texts
 * here are made up.
 */

import { HttpResponse, http } from "msw";
import type { components } from "../api/schema.gen";
import { jsonBody, nextId, noContent, problem, text } from "./fakeHttp";

const API = "*/api/v1";
const MIB = 1024 * 1024;

type Schemas = components["schemas"];
type Role = Schemas["Role"];
export type FakeDocument = Schemas["DocumentOut"];
type Status = Schemas["DocumentStatus"];
type Failure = Schemas["DocumentFailure"];
type EntityType = Schemas["EntityType"];
type Span = Schemas["SpanOut"];
type Exemption = Schemas["ExemptionOut"];
export type FakeMaskingRule = Schemas["MaskingRuleOut"];

export const UNSUPPORTED =
	"This kind of file isn't accepted. Upload a PDF, a Word document (.docx), or plain text or Markdown in UTF-8.";
export const MASKING_CHANGED =
	"The masking changed since you looked at it. Reload it and review again.";
export const NOT_YOUR_ROLE = "Your role in this project does not allow this.";
export const DUPLICATE_RULE = "This project already masks that term.";
export const REVIEW_NOTICE =
	"You're seeing original text. This view is recorded. Masking is not anonymity: text that is only in images was not read.";

/** A value the fake's detectors find wherever it occurs, exactly as written. */
export interface Found {
	value: string;
	type: EntityType;
	source?: string;
}

/** A document's text as read from the file, and what the detectors find in it. */
export interface Original {
	text: string;
	found: readonly Found[];
}

/** The text a document gets when the test does not say. */
export const EXAMPLE_ORIGINAL: Original = {
	text: "Interview on 12 March with amina@example.org. Kofi Mensah leads the team. MRN 448-1207 was referred.",
	found: [
		{ value: "12 March", type: "DATE", source: "dates@1" },
		{ value: "amina@example.org", type: "EMAIL" },
		{ value: "Kofi Mensah", type: "PERSON", source: "names@1" },
		{ value: "448-1207", type: "MEDICAL_RECORD" },
	],
};

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

/** domain/masking.py `STRUCTURED_ENTITY_TYPES`: a detector's finding of these stays. */
const STRUCTURED: readonly EntityType[] = [
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

const ENTITY_TYPES: readonly EntityType[] = [
	"PERSON",
	"LOCATION",
	"DATE",
	...STRUCTURED,
];

interface Access {
	refuseUnlessSignedIn: () => Response | null;
	roleIn: (projectId: string) => Role | null;
	userId: () => string | null;
}

interface Stored {
	projectId: string;
	document: FakeDocument;
	/** `null` until the worker has read the file. */
	original: string | null;
	spans: Span[];
	removed: Exemption[];
}

interface Masked {
	text: string;
	items: Schemas["MaskedItemOut"][];
	counts: Record<string, number>;
}

export interface DocumentOptions {
	status?: Status;
	uploadedBy?: string;
	sizeBytes?: number;
	fileType?: Schemas["DocumentType"];
	original?: Original;
	failure?: Failure;
	isTruncated?: boolean;
}

const escapeRegExp = (value: string) =>
	value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const canRemove = (source: string, type: EntityType): boolean =>
	source.startsWith("user:") ||
	(!source.startsWith("rule:") && !STRUCTURED.includes(type));

const span = (
	start: number,
	end: number,
	entity_type: EntityType,
	source: string,
): Span => ({
	start,
	end,
	entity_type,
	source,
	can_remove: canRemove(source, entity_type),
});

function detect(original: Original): Span[] {
	return original.found.flatMap(({ value, type, source = "patterns@1" }) =>
		[...original.text.matchAll(new RegExp(escapeRegExp(value), "g"))].map(
			(match) => span(match.index, match.index + value.length, type, source),
		),
	);
}

/** domain/masking.py `rule_spans`: whole words, any case. */
function ruleSpans(raw: string, rules: readonly FakeMaskingRule[]): Span[] {
	return rules.flatMap((rule) => {
		const words = rule.term.split(/\s+/).map((w) => escapeRegExp(w));
		const pattern = new RegExp(`(?<!\\w)${words.join("\\s+")}(?!\\w)`, "giu");
		return [...raw.matchAll(pattern)].map((match) =>
			span(
				match.index,
				match.index + match[0].length,
				rule.entity_type,
				`rule:${rule.id}`,
			),
		);
	});
}

/** domain/masking.py `apply_spans`: overlaps merge, one value keeps one placeholder. */
function applySpans(raw: string, spans: readonly Span[]): Masked {
	const ordered = [...spans].sort((a, b) => a.start - b.start || b.end - a.end);
	const regions: { start: number; end: number; type: EntityType }[] = [];
	for (const s of ordered) {
		const last = regions.at(-1);
		if (last !== undefined && s.start < last.end) {
			last.end = Math.max(last.end, s.end);
		} else {
			regions.push({ start: s.start, end: s.end, type: s.entity_type });
		}
	}
	const numbers = new Map<string, number>();
	const counts: Record<string, number> = {};
	const items: Schemas["MaskedItemOut"][] = [];
	let masked = "";
	let cursor = 0;
	for (const region of regions) {
		const key = `${region.type} ${raw.slice(region.start, region.end).toLowerCase()}`;
		if (!numbers.has(key)) {
			counts[region.type] = (counts[region.type] ?? 0) + 1;
			numbers.set(key, counts[region.type] ?? 1);
		}
		masked += raw.slice(cursor, region.start);
		const placeholder = `[${region.type}_${numbers.get(key)}]`;
		items.push({
			start: masked.length,
			end: masked.length + placeholder.length,
			placeholder,
			entity_type: region.type,
			sources: ordered
				.filter((s) => s.start < region.end && s.end > region.start)
				.map((s) => s.source),
		});
		masked += placeholder;
		cursor = region.end;
	}
	return { text: masked + raw.slice(cursor), items, counts };
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

const isEntityType = (value: unknown): value is EntityType =>
	ENTITY_TYPES.some((type) => type === value);

export class FakeDocuments {
	readonly all: Stored[] = [];
	/** Every project's masking rules, oldest first. */
	readonly rules: (FakeMaskingRule & { projectId: string })[] = [];
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
			character_count: null,
			is_truncated: options.isTruncated ?? false,
			masked_items: {},
			mask_version: 0,
			last_review: null,
		};
		const stored: Stored = {
			projectId,
			document,
			original: null,
			spans: [],
			removed: [],
		};
		this.all.push(stored);
		if (["awaiting_review", "approved", "rejected"].includes(status)) {
			this.read(stored, options.original ?? EXAMPLE_ORIGINAL);
		}
		return document;
	}

	/** The worker's part: scanned, read and masked, now waiting for an owner. */
	finishProcessing(documentId: string, original = EXAMPLE_ORIGINAL): void {
		const stored = this.find(documentId);
		this.read(stored, original);
		Object.assign(stored.document, {
			status: "awaiting_review",
			scanned_with: "none",
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

	/** Arrange a masking rule, as if an owner had added it. */
	addRule(
		projectId: string,
		term: string,
		entityType: EntityType = "PERSON",
	): FakeMaskingRule {
		const rule = {
			id: nextId("mr"),
			term,
			entity_type: entityType,
			created_by: this.access.userId() ?? nextId("u"),
			created_at: "2026-11-05T09:30:00Z",
		};
		this.rules.push({ projectId, ...rule });
		return rule;
	}

	/** The masked text, as the API would show it now. */
	maskedText(documentId: string): string {
		const { original, spans } = this.find(documentId);
		return original === null ? "" : applySpans(original, spans).text;
	}

	private read(stored: Stored, original: Original): void {
		stored.original = original.text;
		stored.spans = [
			...detect(original),
			...ruleSpans(original.text, this.rulesIn(stored.projectId)),
		];
		Object.assign(stored.document, {
			character_count: original.text.length,
			masked_items: applySpans(original.text, stored.spans).counts,
			mask_version: 1,
		} satisfies Partial<FakeDocument>);
	}

	private rulesIn(projectId: string): FakeMaskingRule[] {
		return this.rules
			.filter((r) => r.projectId === projectId)
			.map(({ projectId: _, ...rule }) => rule);
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

	/** The gate, then 403 for anyone but an owner. */
	private ownerGate(projectId: string): Response | null {
		const role = this.gate(projectId);
		if (role instanceof Response) {
			return role;
		}
		return role === "owner" ? null : problem(403, NOT_YOUR_ROLE);
	}

	private storedIn(projectId: string, documentId: string): Stored | undefined {
		return this.all.find(
			(s) => s.projectId === projectId && s.document.id === documentId,
		);
	}

	private review(stored: Stored): Schemas["DocumentReviewOut"] {
		const raw = stored.original ?? "";
		const masked = applySpans(raw, stored.spans);
		return {
			document_id: stored.document.id,
			tag: stored.document.tag,
			status: stored.document.status,
			mask_version: stored.document.mask_version,
			raw_text: raw,
			masked_text: masked.text,
			spans: [...stored.spans].sort((a, b) => a.start - b.start),
			removed: stored.removed,
			items: masked.items,
			page_starts: [0],
			is_truncated: stored.document.is_truncated,
			notice: REVIEW_NOTICE,
		};
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
		const refused = this.ownerGate(projectId);
		if (refused !== null) {
			return refused;
		}
		const stored = this.storedIn(projectId, documentId);
		if (stored === undefined) {
			return problem(404, "Not Found");
		}
		this.remove(documentId);
		return noContent();
	}

	private async decide(
		projectId: string,
		documentId: string,
		decision: Schemas["DocumentDecision"],
		request: Request,
	) {
		const refused = this.ownerGate(projectId);
		if (refused !== null) {
			return refused;
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

	/** application/documents.py `_correct`: a new version, masked again with the rules. */
	private async correctMasks(
		projectId: string,
		documentId: string,
		request: Request,
		change: (
			stored: Stored,
			body: Record<string, unknown>,
			raw: string,
		) => Response | null,
	) {
		const refused = this.ownerGate(projectId);
		if (refused !== null) {
			return refused;
		}
		const stored = this.storedIn(projectId, documentId);
		if (stored?.original == null) {
			return problem(404, "Not Found");
		}
		const { document } = stored;
		if (!["awaiting_review", "approved"].includes(document.status)) {
			return problem(
				409,
				STATE_MESSAGES[document.status] ?? "This document can't be corrected.",
			);
		}
		const body = await jsonBody(request);
		if (body["version"] !== document.mask_version) {
			return problem(409, MASKING_CHANGED);
		}
		const refusal = change(stored, body, stored.original);
		if (refusal !== null) {
			return refusal;
		}
		const fromRules = ruleSpans(stored.original, this.rulesIn(projectId));
		stored.spans = [
			...stored.spans,
			...fromRules.filter(
				(r) =>
					!stored.spans.some(
						(s) =>
							s.start === r.start && s.end === r.end && s.source === r.source,
					),
			),
		];
		Object.assign(document, {
			mask_version: document.mask_version + 1,
			status: "awaiting_review",
			masked_items: applySpans(stored.original, stored.spans).counts,
		} satisfies Partial<FakeDocument>);
		return HttpResponse.json(this.review(stored));
	}

	private addMask(stored: Stored, body: Record<string, unknown>, raw: string) {
		const { start, end, entity_type: type } = body;
		if (typeof start !== "number" || typeof end !== "number") {
			return problem(422, "The request body is not valid.");
		}
		if (!isEntityType(type)) {
			return problem(422, "The request body is not valid.");
		}
		if (!(start >= 0 && start < end && end <= raw.length)) {
			return problem(422, "The range to mask must lie within the text");
		}
		if (raw.slice(start, end).trim() === "") {
			return problem(422, "The range to mask must contain text");
		}
		stored.spans.push(span(start, end, type, `user:${this.access.userId()}`));
		return null;
	}

	private unmask(stored: Stored, body: Record<string, unknown>) {
		const reason = text(body, "reason").split(/\s+/).join(" ").trim();
		if (reason === "") {
			return problem(422, "The reason must not be blank");
		}
		const matching = stored.spans.filter(
			(s) => s.start === body["start"] && s.end === body["end"],
		);
		if (matching.length === 0) {
			return problem(422, "No mask covers exactly that range");
		}
		if (matching.some((s) => s.source.startsWith("rule:"))) {
			return problem(
				422,
				"A project masking rule made this mask; remove the rule to stop masking it",
			);
		}
		if (matching.some((s) => !s.can_remove)) {
			return problem(
				422,
				"Structured identifiers (emails, phone numbers, ID numbers…) stay masked",
			);
		}
		stored.spans = stored.spans.filter((s) => !matching.includes(s));
		stored.removed.push(
			...matching.map((s) => ({
				start: s.start,
				end: s.end,
				entity_type: s.entity_type,
				source: s.source,
				removed_by: this.access.userId() ?? "",
				reason,
			})),
		);
		return null;
	}

	private async createRule(projectId: string, request: Request) {
		const refused = this.ownerGate(projectId);
		if (refused !== null) {
			return refused;
		}
		const body = await jsonBody(request);
		const term = text(body, "term").split(/\s+/).join(" ").trim();
		const type = body["entity_type"] ?? "PERSON";
		if (!isEntityType(type)) {
			return problem(422, "The request body is not valid.");
		}
		if (term.length < 2) {
			return problem(422, "MaskingRule.term must be at least 2 characters");
		}
		if (
			this.rulesIn(projectId).some(
				(r) => r.term.toLowerCase() === term.toLowerCase(),
			)
		) {
			return problem(409, DUPLICATE_RULE);
		}
		const rule = this.addRule(projectId, term, type);
		return HttpResponse.json(rule, {
			status: 201,
			headers: {
				location: `/api/v1/projects/${projectId}/masking-rules/${rule.id}`,
			},
		});
	}

	handlers() {
		const documents = `${API}/projects/:projectId/documents`;
		const one = `${documents}/:documentId`;
		const rules = `${API}/projects/:projectId/masking-rules`;
		const ids = (params: Record<string, unknown>) =>
			[String(params["projectId"]), String(params["documentId"])] as const;
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
				const refused = this.ownerGate(projectId);
				return refused ?? this.upload(projectId, request);
			}),

			http.get(one, ({ params }) => {
				const [projectId, documentId] = ids(params);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				const stored = this.storedIn(projectId, documentId);
				return stored === undefined
					? problem(404, "Not Found")
					: HttpResponse.json(stored.document);
			}),

			http.delete(one, ({ params }) => this.delete(...ids(params))),

			http.get(`${one}/masked`, ({ params }) => {
				const [projectId, documentId] = ids(params);
				const role = this.gate(projectId);
				if (role instanceof Response) {
					return role;
				}
				const stored = this.storedIn(projectId, documentId);
				if (stored?.original == null) {
					return problem(404, "Not Found");
				}
				const { document } = stored;
				const masked = applySpans(stored.original, stored.spans);
				return HttpResponse.json({
					document_id: document.id,
					tag: document.tag,
					status: document.status,
					mask_version: document.mask_version,
					masked_text: masked.text,
					items: masked.items,
					page_starts: [0],
					is_truncated: document.is_truncated,
				} satisfies Schemas["MaskedDocumentOut"]);
			}),

			http.get(`${one}/review`, ({ params }) => {
				const [projectId, documentId] = ids(params);
				const refused = this.ownerGate(projectId);
				if (refused !== null) {
					return refused;
				}
				const stored = this.storedIn(projectId, documentId);
				return stored?.original == null
					? problem(404, "Not Found")
					: HttpResponse.json(this.review(stored));
			}),

			http.post(`${one}/masks`, ({ params, request }) =>
				this.correctMasks(...ids(params), request, (stored, body, raw) =>
					this.addMask(stored, body, raw),
				),
			),

			http.post(`${one}/unmask`, ({ params, request }) =>
				this.correctMasks(...ids(params), request, (stored, body) =>
					this.unmask(stored, body),
				),
			),

			http.post(`${one}/approve`, ({ params, request }) =>
				this.decide(...ids(params), "approve", request),
			),

			http.post(`${one}/reject`, ({ params, request }) =>
				this.decide(...ids(params), "reject", request),
			),

			http.get(rules, ({ params }) => {
				const projectId = String(params["projectId"]);
				return (
					this.ownerGate(projectId) ??
					HttpResponse.json(this.rulesIn(projectId))
				);
			}),

			http.post(rules, ({ params, request }) =>
				this.createRule(String(params["projectId"]), request),
			),

			http.delete(`${rules}/:ruleId`, ({ params }) => {
				const projectId = String(params["projectId"]);
				const refused = this.ownerGate(projectId);
				if (refused !== null) {
					return refused;
				}
				const index = this.rules.findIndex(
					(r) => r.projectId === projectId && r.id === params["ruleId"],
				);
				if (index === -1) {
					return problem(404, "Not Found");
				}
				this.rules.splice(index, 1);
				return noContent();
			}),
		];
	}
}
