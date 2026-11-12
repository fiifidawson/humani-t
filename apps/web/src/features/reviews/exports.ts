/**
 * Links to a review's downloads (API `GET …/exports/{name}`). Plain links: the
 * browser fetches the file with the signed-in session, and the API records each
 * download in the audit log. What may be downloaded is the API's decision.
 */

export interface Download {
	label: string;
	href: string;
	/** Opens in a new tab rather than downloading: the brief to print or save as PDF. */
	isPrintable?: boolean;
}

type ExportName =
	| "dataset.csv"
	| "dataset.jsonl"
	| "included.ris"
	| "prisma.md"
	| "manifest.json"
	| "brief.md"
	| "brief.html"
	| "brief.json";

function exportHref(reviewId: string, name: ExportName, runId: string): string {
	const query = new URLSearchParams({ run_id: runId });
	return `/api/v1/reviews/${encodeURIComponent(reviewId)}/exports/${name}?${query}`;
}

export function paperDownloads(reviewId: string, runId: string): Download[] {
	return [
		{ label: "Papers (CSV)", href: exportHref(reviewId, "dataset.csv", runId) },
		{
			label: "Papers (JSON lines)",
			href: exportHref(reviewId, "dataset.jsonl", runId),
		},
		{
			label: "Included papers for a reference manager (RIS)",
			href: exportHref(reviewId, "included.ris", runId),
		},
		{ label: "PRISMA report", href: exportHref(reviewId, "prisma.md", runId) },
		{ label: "Run record", href: exportHref(reviewId, "manifest.json", runId) },
	];
}

export function briefDownloads(reviewId: string, runId: string): Download[] {
	return [
		{
			label: "Brief (Markdown)",
			href: exportHref(reviewId, "brief.md", runId),
		},
		{
			label: "Brief to print or save as PDF",
			href: exportHref(reviewId, "brief.html", runId),
			isPrintable: true,
		},
		{ label: "Brief (JSON)", href: exportHref(reviewId, "brief.json", runId) },
	];
}
