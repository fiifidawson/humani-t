/**
 * The one way the web app talks to the API, typed by the generated schema.
 *
 * It adds what every request needs: the page's own origin, JSON bodies (a document
 * upload is the one exception) and the CSRF token on writes. It knows nothing about
 * screens.
 */

import createClient, { type Client, type Middleware } from "openapi-fetch";
import { apiErrorFrom, offlineError } from "./problem";
import type { paths } from "./schema.gen";

export const CSRF_COOKIE = "__Host-csrf";
const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export type ApiClient = Client<paths>;

/** The value of one cookie, or `null`. Only readable cookies are visible: the session cookie never is. */
export function readCookie(
	name: string,
	cookies: string = document.cookie,
): string | null {
	for (const pair of cookies.split(";")) {
		const separator = pair.indexOf("=");
		if (separator > 0 && pair.slice(0, separator).trim() === name) {
			return decodeURIComponent(pair.slice(separator + 1).trim());
		}
	}
	return null;
}

const sendCsrfToken: Middleware = {
	onRequest({ request }) {
		// Read on every request, never cached: the API rotates the token at
		// sign-in and at step-up, and a stale copy would fail every write.
		const token = readCookie(CSRF_COOKIE);
		if (!SAFE_METHODS.has(request.method) && token !== null) {
			request.headers.set(CSRF_HEADER, token);
		}
		return request;
	},
};

export class NonJsonBodyError extends Error {
	override name = "NonJsonBodyError";
}

async function isJson(request: Request): Promise<boolean> {
	if (request.headers.get("content-type") !== "application/json") {
		return false;
	}
	try {
		JSON.parse(await request.clone().text());
		return true;
	} catch {
		return false;
	}
}

// The API admits multipart on this one path and nowhere else
// (services/api/app/api/routes/documents.py, UPLOAD_PATH); so does the client.
const UPLOAD_PATH = /^\/api\/v1\/projects\/[^/]+\/documents\/?$/;

function isDocumentUpload(request: Request): boolean {
	return (
		request.method === "POST" &&
		UPLOAD_PATH.test(new URL(request.url).pathname) &&
		(request.headers.get("content-type") ?? "").startsWith(
			"multipart/form-data;",
		)
	);
}

const sendJsonOnly: Middleware = {
	// The header alone is not enough: a caller can swap the serializer and
	// still carry the default JSON content type. Bodies are capped at 1 MiB by
	// the API, so parsing a copy costs little.
	async onRequest({ request }) {
		if (
			request.body !== null &&
			!isDocumentUpload(request) &&
			!(await isJson(request))
		) {
			throw new NonJsonBodyError(
				"Refusing to send a body that is not JSON: the API accepts JSON only.",
			);
		}
		return request;
	},
};

export function createApiClient(): ApiClient {
	// An absolute URL on the page's own origin: the SPA and the API are one
	// origin in every deployment (Caddy in production, Vite's proxy in dev).
	const client = createClient<paths>({
		baseUrl: window.location.origin,
		headers: { Accept: "application/json, application/problem+json" },
		credentials: "same-origin",
	});
	client.use(sendCsrfToken, sendJsonOnly);
	return client;
}

interface Answer<Data> {
	data?: Data;
	error?: unknown;
	response: Response;
}

async function settle<Data>(
	request: Promise<Answer<Data>>,
): Promise<Answer<Data>> {
	try {
		return await request;
	} catch (error) {
		// fetch rejects with a TypeError only when no response arrived at all.
		if (error instanceof TypeError) {
			throw offlineError(error);
		}
		throw error;
	}
}

/** The response body, or an ApiError carrying the problem document. */
export async function unwrap<Data>(
	request: Promise<Answer<Data>>,
): Promise<Data> {
	const { data, error, response } = await settle(request);
	if (!response.ok) {
		throw apiErrorFrom(response, error);
	}
	if (data === undefined) {
		throw new Error(
			`The API answered ${response.status} with no body where one was expected.`,
		);
	}
	return data;
}

/** For 204 answers: resolve on success, or throw the ApiError. */
export async function unwrapEmpty(
	request: Promise<Answer<unknown>>,
): Promise<void> {
	const { error, response } = await settle(request);
	if (!response.ok) {
		throw apiErrorFrom(response, error);
	}
}
