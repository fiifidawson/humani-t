import { describe, expect, it } from "vitest";
import { readSignIn } from "./signInOutcome";

const user = {
	id: "u-1",
	email: "amara@example.org",
	display_name: "Amara N.",
	is_admin: false,
};

describe("reading the sign-in answer", () => {
	it("keeps the account on a completed sign-in", () => {
		expect(readSignIn({ status: "signed_in", user })).toEqual({
			status: "signed_in",
			user,
		});
	});

	it("keeps only the second factors this screen knows how to ask for", () => {
		expect(
			readSignIn({
				status: "mfa_required",
				methods: ["totp", "sms", "passkey"],
			}),
		).toEqual({ status: "mfa_required", methods: ["totp", "passkey"] });
	});

	it("refuses a completed sign-in that names no account", () => {
		expect(() => readSignIn({ status: "signed_in", user: null })).toThrow(
			"without the account",
		);
	});
});
