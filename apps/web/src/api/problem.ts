/**
 * API failures as one error type, read from RFC 9457 problem documents.
 *
 * Every error the API sends is a problem document, written in plain language
 * by the API (services/api/app/api/errors.py). This module reads it, decides
 * which of the UI_UX_SPEC §11.4 cases it is, and picks the words a person
 * sees. It does not decide what a screen does about it.
 */

const STATUS_TITLES: Readonly<Record<number, string>> = {
	400: "Bad Request",
	401: "Unauthorized",
	403: "Forbidden",
	404: "Not Found",
	409: "Conflict",
	413: "Content Too Large",
	415: "Unsupported Media Type",
	422: "Unprocessable Content",
	429: "Too Many Requests",
	500: "Internal Server Error",
	502: "Bad Gateway",
	503: "Service Unavailable",
};

// No response arrived, so there is no HTTP status; 0 is what fetch reports then.
const OFFLINE_STATUS = 0;

export interface FieldProblem {
	loc: (string | number)[];
	msg: string;
}

export interface Problem {
	title: string;
	status: number;
	detail?: string;
	errors?: FieldProblem[];
	violations?: string[];
	reauthentication_required?: boolean;
	mfa_required?: boolean;
	mfa_enrolment_required?: boolean;
	last_owner?: boolean;
}

export type FailureKind =
	| "invalid"
	| "signed-out"
	| "reauthentication-required"
	| "mfa-required"
	| "mfa-enrolment-required"
	| "forbidden"
	| "not-found"
	| "conflict"
	| "throttled"
	| "server"
	| "offline";

export class ApiError extends Error {
	readonly kind: FailureKind;
	readonly status: number;
	readonly problem: Problem;
	/** Seconds from `Retry-After`, or `null` when the API did not say. */
	readonly retryAfterSeconds: number | null;
	/** The `x-request-id` to quote to support, or `null` when there was no response. */
	readonly requestId: string | null;

	constructor(
		problem: Problem,
		retryAfterSeconds: number | null,
		requestId: string | null,
	) {
		super(problem.detail ?? problem.title);
		this.name = "ApiError";
		this.problem = problem;
		this.status = problem.status;
		this.retryAfterSeconds = retryAfterSeconds;
		this.requestId = requestId;
		this.kind = classify(problem, retryAfterSeconds);
	}
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === "object" && value !== null && !Array.isArray(value);

const isTrue = (record: Record<string, unknown>, key: string) =>
	record[key] === true;

function readFieldProblems(value: unknown): FieldProblem[] | undefined {
	if (!Array.isArray(value)) {
		return undefined;
	}
	return value.flatMap((item: unknown) => {
		if (
			!isRecord(item) ||
			typeof item["msg"] !== "string" ||
			!Array.isArray(item["loc"])
		) {
			return [];
		}
		const loc = item["loc"].filter(
			(part: unknown): part is string | number =>
				typeof part === "string" || typeof part === "number",
		);
		return [{ loc, msg: item["msg"] }];
	});
}

/** Read whatever came back into a Problem; a body that is not one keeps only the status. */
export function parseProblem(body: unknown, status: number): Problem {
	const title = STATUS_TITLES[status] ?? "Error";
	if (!isRecord(body)) {
		return { title, status };
	}
	const problem: Problem = {
		title: typeof body["title"] === "string" ? body["title"] : title,
		status,
	};
	if (typeof body["detail"] === "string") {
		problem.detail = body["detail"];
	}
	const errors = readFieldProblems(body["errors"]);
	if (errors !== undefined) {
		problem.errors = errors;
	}
	if (Array.isArray(body["violations"])) {
		problem.violations = body["violations"].filter(
			(v: unknown): v is string => typeof v === "string",
		);
	}
	for (const flag of [
		"reauthentication_required",
		"mfa_required",
		"mfa_enrolment_required",
		"last_owner",
	] as const) {
		if (isTrue(body, flag)) {
			problem[flag] = true;
		}
	}
	return problem;
}

function classify(
	problem: Problem,
	retryAfterSeconds: number | null,
): FailureKind {
	const { status } = problem;
	if (status === OFFLINE_STATUS) {
		return "offline";
	}
	if ((status === 429 || status === 503) && retryAfterSeconds !== null) {
		return "throttled";
	}
	if (status === 401) {
		if (problem.reauthentication_required) {
			return "reauthentication-required";
		}
		return problem.mfa_required ? "mfa-required" : "signed-out";
	}
	if (status === 403) {
		return problem.mfa_enrolment_required
			? "mfa-enrolment-required"
			: "forbidden";
	}
	if (status === 400 || status === 422) {
		return "invalid";
	}
	if (status === 404) {
		return "not-found";
	}
	if (status === 409) {
		return "conflict";
	}
	return status >= 500 ? "server" : "invalid";
}

function readRetryAfter(response: Response): number | null {
	const header = response.headers.get("retry-after");
	if (header === null || !/^\d+$/.test(header.trim())) {
		return null;
	}
	return Number(header.trim());
}

export function apiErrorFrom(response: Response, body: unknown): ApiError {
	return new ApiError(
		parseProblem(body, response.status),
		readRetryAfter(response),
		response.headers.get("x-request-id"),
	);
}

export function offlineError(cause: unknown): ApiError {
	const error = new ApiError(
		{ title: "Offline", status: OFFLINE_STATUS },
		null,
		null,
	);
	error.cause = cause;
	return error;
}

const NOT_FOUND = "This project doesn't exist or you don't have access.";
const OFFLINE = "You're offline. Check your connection and try again.";
const SERVER =
	"Something went wrong on the server. If it keeps happening, quote the request id.";

/** The sentence to show a person. The API writes it, except where the spec fixes the words. */
export function userMessage(error: ApiError): string {
	switch (error.kind) {
		case "offline":
			return OFFLINE;
		case "not-found":
			return NOT_FOUND;
		case "server":
			return error.problem.detail ?? SERVER;
		default:
			return error.problem.detail ?? error.problem.title;
	}
}

/** Validation messages keyed by the field they concern: `["body", "name"]` becomes `name`. */
export function fieldErrors(error: ApiError): Record<string, string> {
	const fields: Record<string, string> = {};
	for (const { loc, msg } of error.problem.errors ?? []) {
		const field = loc.at(-1);
		if (field !== undefined && !(String(field) in fields)) {
			fields[String(field)] = msg;
		}
	}
	return fields;
}

export function isApiError(
	error: unknown,
	...kinds: FailureKind[]
): error is ApiError {
	return (
		error instanceof ApiError &&
		(kinds.length === 0 || kinds.includes(error.kind))
	);
}
