/**
 * The reads and the upload of a project's documents. Apart from queries.ts so
 * that only the lazily loaded documents page carries them; their keys stay in
 * `keys` with every other key.
 */

import { queryOptions } from "@tanstack/react-query";
import { type ApiClient, unwrap } from "../api/client";
import type { components } from "../api/schema.gen";
import { keys } from "./queries";

export type DocumentListing = components["schemas"]["DocumentListOut"];
export type ProjectDocument = components["schemas"]["DocumentOut"];
export type DocumentStatus = components["schemas"]["DocumentStatus"];

/** A worker is still scanning, reading or masking it. */
export const IN_PROGRESS: readonly DocumentStatus[] = [
	"uploaded",
	"scanning",
	"extracting",
	"masking",
];

export const isInProgress = (document: ProjectDocument): boolean =>
	IN_PROGRESS.includes(document.status);

export const documentsQuery = (
	api: ApiClient,
	projectId: string,
	pollIntervalMs: number,
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
