/**
 * The reads and the upload of a project's documents, and its masking rules.
 * Apart from queries.ts so that only the screens using them carry them; their
 * keys stay in `keys` with every other key.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";
import type { components } from "../api/schema.gen";
import { keys } from "./queries";

export type DocumentListing = components["schemas"]["DocumentListOut"];
export type ProjectDocument = components["schemas"]["DocumentOut"];
export type DocumentStatus = components["schemas"]["DocumentStatus"];
export type DocumentReview = components["schemas"]["DocumentReviewOut"];
export type MaskingRule = components["schemas"]["MaskingRuleOut"];

/** A worker is still scanning, reading or masking it. */
export const IN_PROGRESS: readonly DocumentStatus[] = [
	"uploaded",
	"scanning",
	"extracting",
	"masking",
];

export const isInProgress = (document: ProjectDocument): boolean =>
	IN_PROGRESS.includes(document.status);

/** `pollIntervalMs` false: never look again on a timer. */
export const documentsQuery = (
	api: ApiClient,
	projectId: string,
	pollIntervalMs: number | false,
) =>
	queryOptions({
		queryKey: keys.documents(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/documents", {
					params: { path: { project_id: projectId } },
				}),
			),
		// The worker moves a document on without telling anyone; look again
		// only while something is moving, so an idle page costs nothing.
		refetchInterval: (query) =>
			query.state.data?.documents.some(isInProgress) ? pollIntervalMs : false,
	});

export const maskedDocumentQuery = (
	api: ApiClient,
	projectId: string,
	documentId: string,
) =>
	queryOptions({
		queryKey: keys.maskedDocument(projectId, documentId),
		queryFn: () =>
			unwrap(
				api.GET(
					"/api/v1/projects/{project_id}/documents/{document_id}/masked",
					{
						params: {
							path: { project_id: projectId, document_id: documentId },
						},
					},
				),
			),
		// A correction by another owner makes a new version; the decision
		// must name the version on screen, so never show a cached one.
		staleTime: 0,
	});

// Raw text: the API audits every fetch, so fetch once per opening, and drop it
// from memory as soon as nothing shows it.
const RAW_TEXT_CACHE = {
	staleTime: Number.POSITIVE_INFINITY,
	gcTime: 0,
	refetchOnWindowFocus: false,
	refetchOnReconnect: false,
} as const;

/** The original text and every mask over it. Owners only. */
export const documentReviewQuery = (
	api: ApiClient,
	projectId: string,
	documentId: string,
) =>
	queryOptions({
		queryKey: keys.documentReview(projectId, documentId),
		queryFn: () =>
			unwrap(
				api.GET(
					"/api/v1/projects/{project_id}/documents/{document_id}/review",
					{
						params: {
							path: { project_id: projectId, document_id: documentId },
						},
					},
				),
			),
		...RAW_TEXT_CACHE,
	});

/** Owners only: a rule's term is usually a name. */
export const maskingRulesQuery = (api: ApiClient, projectId: string) =>
	queryOptions({
		queryKey: keys.maskingRules(projectId),
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/projects/{project_id}/masking-rules", {
					params: { path: { project_id: projectId } },
				}),
			),
		...RAW_TEXT_CACHE,
	});

export function addMaskingRule(
	api: ApiClient,
	projectId: string,
	rule: components["schemas"]["MaskingRuleIn"],
): Promise<MaskingRule> {
	return unwrap(
		api.POST("/api/v1/projects/{project_id}/masking-rules", {
			params: { path: { project_id: projectId } },
			body: rule,
		}),
	);
}

export function uploadDocument(
	api: ApiClient,
	projectId: string,
	file: File,
): Promise<ProjectDocument> {
	const form = new FormData();
	form.append("file", file);
	return unwrap(
		api.POST("/api/v1/projects/{project_id}/documents", {
			params: { path: { project_id: projectId } },
			// The schema types a binary part as a string; the serializer sends
			// the file itself, and the browser writes the multipart boundary.
			body: { file: file.name },
			bodySerializer: () => form,
		}),
	);
}
