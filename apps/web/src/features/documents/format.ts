/**
 * Documents in words: a status as a person would say it, and a size.
 * The API's own sentences (a failure's message, a refusal) are shown as sent.
 */

import type { DocumentStatus } from "../../app/documentQueries";

const STATUS_WORDS: Readonly<Record<DocumentStatus, string>> = {
	uploaded: "Checking for viruses",
	scanning: "Checking for viruses",
	extracting: "Reading the text",
	masking: "Reading the text",
	awaiting_review: "Waiting for privacy review",
	approved: "Ready",
	rejected: "Rejected",
	failed: "Failed",
};

export const statusWords = (status: DocumentStatus): string =>
	STATUS_WORDS[status];

const KB = 1024;
const MB = 1024 * KB;

/** "820 bytes", "12 KB", "3.5 MB", "20 MB". Binary units, as the API counts them. */
export function formatSize(bytes: number): string {
	if (bytes < KB) {
		return `${bytes} bytes`;
	}
	if (bytes < MB) {
		return `${Math.round(bytes / KB)} KB`;
	}
	const megabytes = bytes / MB;
	return Number.isInteger(megabytes)
		? `${megabytes} MB`
		: `${megabytes.toFixed(1)} MB`;
}
