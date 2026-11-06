import { describe, expect, it } from "vitest";
import {
	type ApiError,
	apiErrorFrom,
	fieldErrors,
	offlineError,
	userMessage,
} from "./problem";

function response(
	status: number,
	headers: Record<string, string> = {},
): Response {
	return new Response(null, { status, headers });
}

function errorFor(
	status: number,
	body: unknown,
	headers: Record<string, string> = {},
): ApiError {
	return apiErrorFrom(response(status, headers), body);
}

describe("reading a problem document (RFC 9457)", () => {
	it("keeps the title, detail and status the API wrote", () => {
		const error = errorFor(409, {
			type: "about:blank",
			title: "Conflict",
			status: 409,
			detail: "The record changed while you were editing it.",
		});

		expect(error.status).toBe(409);
		expect(error.problem.title).toBe("Conflict");
		expect(error.problem.detail).toBe(
			"The record changed while you were editing it.",
		);
	});

	it("survives a body that is not a problem document", () => {
		const error = errorFor(502, "<html>Bad gateway</html>");

		expect(error.problem.title).toBe("Bad Gateway");
		expect(error.problem.detail).toBeUndefined();
	});

	it("keeps the request id so support can find the log line", () => {
		const error = errorFor(
			500,
			{ title: "Internal Server Error", status: 500 },
			{ "x-request-id": "abc123" },
		);

		expect(error.requestId).toBe("abc123");
	});
});

describe("what kind of failure it is (UI_UX_SPEC §11.4)", () => {
	it.each([
		[400, {}, "invalid"],
		[422, {}, "invalid"],
		[401, {}, "signed-out"],
		[401, { reauthentication_required: true }, "reauthentication-required"],
		[401, { mfa_required: true }, "mfa-required"],
		[403, { mfa_enrolment_required: true }, "mfa-enrolment-required"],
		[403, {}, "forbidden"],
		[404, {}, "not-found"],
		[409, {}, "conflict"],
		[500, {}, "server"],
		[503, {}, "server"],
	] as const)("a %i with %o is %s", (status, extensions, kind) => {
		expect(errorFor(status, { title: "x", status, ...extensions }).kind).toBe(
			kind,
		);
	});

	it.each([429, 503])(
		"a %i with Retry-After is throttled, with the wait in seconds",
		(status) => {
			const error = errorFor(
				status,
				{ title: "x", status },
				{ "retry-after": "40" },
			);

			expect(error.kind).toBe("throttled");
			expect(error.retryAfterSeconds).toBe(40);
		},
	);

	it("a failed connection is offline", () => {
		expect(offlineError(new TypeError("Failed to fetch")).kind).toBe("offline");
	});
});

describe("the message a person sees", () => {
	it("is the API's own plain-language detail", () => {
		const error = errorFor(409, {
			title: "Conflict",
			status: 409,
			detail: "Someone else already accepted this draft.",
		});

		expect(userMessage(error)).toBe(
			"Someone else already accepted this draft.",
		);
	});

	it("is the same for a missing project and one the person may not see", () => {
		const missing = errorFor(404, {
			title: "Not Found",
			status: 404,
			detail: "Not Found",
		});
		const hidden = errorFor(404, {
			title: "Not Found",
			status: 404,
			detail: "No such project",
		});

		expect(userMessage(missing)).toBe(
			"This project doesn't exist or you don't have access.",
		);
		expect(userMessage(hidden)).toBe(userMessage(missing));
	});

	it("never shows raw text for a server error, only a pointer to the request id", () => {
		const error = errorFor(500, "Traceback (most recent call last): ...", {
			"x-request-id": "r-1",
		});

		expect(userMessage(error)).not.toContain("Traceback");
	});

	it("says so plainly when the network is down", () => {
		expect(userMessage(offlineError(new TypeError("Failed to fetch")))).toBe(
			"You're offline. Check your connection and try again.",
		);
	});
});

describe("validation errors by field", () => {
	it("maps each error to the body field it concerns", () => {
		const error = errorFor(422, {
			title: "Unprocessable Content",
			status: 422,
			errors: [
				{
					loc: ["body", "name"],
					msg: "String should have at most 200 characters",
					type: "string_too_long",
				},
				{
					loc: ["query", "include_archived"],
					msg: "Input should be a valid boolean",
					type: "bool_parsing",
				},
			],
		});

		expect(fieldErrors(error)).toEqual({
			name: "String should have at most 200 characters",
			include_archived: "Input should be a valid boolean",
		});
	});

	it("is empty for anything that is not a validation problem", () => {
		expect(fieldErrors(errorFor(500, { title: "x", status: 500 }))).toEqual({});
	});
});
