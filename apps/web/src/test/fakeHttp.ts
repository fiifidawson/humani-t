/**
 * What every fake API module answers with: problem documents, empty answers,
 * request bodies, and ids shaped like the API's UUIDs.
 */

import { HttpResponse, type JsonBodyType } from "msw";

let sequence = 0;

export const nextId = (prefix: string) =>
	`${prefix}-0000-4000-8000-${String(++sequence).padStart(12, "0")}`;

export const nextNumber = () => ++sequence;

const TITLES: Record<number, string> = {
	400: "Bad Request",
	401: "Unauthorized",
	403: "Forbidden",
	404: "Not Found",
	409: "Conflict",
	422: "Unprocessable Content",
	429: "Too Many Requests",
	500: "Internal Server Error",
	502: "Bad Gateway",
	503: "Service Unavailable",
};

export function problem(
	status: number,
	detail: string,
	extensions: Record<string, JsonBodyType> = {},
	headers: Record<string, string> = {},
) {
	return HttpResponse.json(
		{
			type: "about:blank",
			title: TITLES[status] ?? "Error",
			status,
			detail,
			...extensions,
		},
		{
			status,
			headers: {
				"content-type": "application/problem+json",
				"x-request-id": "req-test-1",
				...headers,
			},
		},
	);
}

export function noContent() {
	return new HttpResponse(null, { status: 204 });
}

export async function jsonBody(
	request: Request,
): Promise<Record<string, unknown>> {
	const text = await request.clone().text();
	if (text === "") {
		return {};
	}
	const parsed: unknown = JSON.parse(text);
	return typeof parsed === "object" && parsed !== null
		? (parsed as Record<string, unknown>)
		: {};
}

export function text(body: Record<string, unknown>, key: string): string {
	const value = body[key];
	return typeof value === "string" ? value : "";
}
