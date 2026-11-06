import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { fakeApi, server } from "../test/server";
import { createApiClient, unwrap, unwrapEmpty } from "./client";
import { ApiError } from "./problem";

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return api;
}

describe("the API client", () => {
	it("talks to /api/v1 on the page's own origin, so cookies stay first-party", async () => {
		const api = signedIn();

		await unwrap(
			createApiClient().GET("/api/v1/projects", { params: { query: {} } }),
		);

		expect(api.requests[0]?.path).toBe("/api/v1/projects");
	});

	it("sends the token from the __Host-csrf cookie as X-CSRF-Token on every write", async () => {
		const api = signedIn();
		const client = createApiClient();

		await unwrap(
			client.POST("/api/v1/projects", {
				body: { name: "Cash vs vouchers, Gao" },
			}),
		);
		await unwrapEmpty(client.POST("/api/v1/account/sessions/revoke-others"));

		expect(api.requestsTo("POST", "/projects")[0]?.csrfToken).toBe(
			api.csrfToken,
		);
		expect(
			api.requestsTo("POST", "/account/sessions/revoke-others")[0]?.csrfToken,
		).toBe(api.csrfToken);
	});

	it("reads the cookie for each request, so a token rotated at step-up is used at once", async () => {
		const api = signedIn();
		const client = createApiClient();
		await unwrap(client.POST("/api/v1/projects", { body: { name: "First" } }));

		api.signInAs(api.users[0] ?? api.addUser());
		await unwrap(client.POST("/api/v1/projects", { body: { name: "Second" } }));

		expect(api.requestsTo("POST", "/projects").map((r) => r.csrfToken)).toEqual(
			[expect.any(String), api.csrfToken],
		);
	});

	it("does not send the CSRF token on reads", async () => {
		const api = signedIn();

		await unwrap(createApiClient().GET("/api/v1/auth/session"));

		expect(api.requests[0]?.csrfToken).toBeNull();
	});

	it("sends bodies as JSON", async () => {
		const api = signedIn();

		await unwrap(
			createApiClient().POST("/api/v1/projects", { body: { name: "Gao" } }),
		);

		expect(api.requests[0]?.contentType).toBe("application/json");
		expect(api.requests[0]?.body).toEqual({ name: "Gao" });
	});

	it("refuses to send a body that is not JSON", async () => {
		signedIn();
		const client = createApiClient();

		const sending = client.POST("/api/v1/projects", {
			body: { name: "Gao" },
			bodySerializer: () => new URLSearchParams({ name: "Gao" }),
		});

		await expect(sending).rejects.toThrow("JSON");
	});

	it("turns a problem document into an ApiError", async () => {
		const api = fakeApi();
		api.addUser();

		const signingIn = unwrap(
			createApiClient().POST("/api/v1/auth/login", {
				body: {
					email: "amara@example.org",
					password: "wrong password entirely",
				},
			}),
		);

		await expect(signingIn).rejects.toBeInstanceOf(ApiError);
		await expect(signingIn).rejects.toMatchObject({
			status: 401,
			kind: "signed-out",
		});
	});

	it("turns a dropped connection into an offline error", async () => {
		server.use(http.get("*/api/v1/projects", () => HttpResponse.error()));

		const loading = unwrap(
			createApiClient().GET("/api/v1/projects", { params: { query: {} } }),
		);

		await expect(loading).rejects.toMatchObject({ kind: "offline" });
	});
});
