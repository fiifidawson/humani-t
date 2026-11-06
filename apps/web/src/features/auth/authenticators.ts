/**
 * Adding a sign-in method, shared by first-sign-in enrolment and the Account
 * page. Each step is a request to the API; the browser only runs the passkey
 * ceremony in between.
 */

import { type ApiClient, unwrap } from "../../api/client";
import { createPasskey } from "./webauthn";

export async function registerPasskey(api: ApiClient, name: string) {
	const { options } = await unwrap(
		api.POST("/api/v1/account/mfa/passkeys/options"),
	);
	const credential = await createPasskey(options);
	return unwrap(
		api.POST("/api/v1/account/mfa/passkeys", { body: { name, credential } }),
	);
}

export function startTotp(api: ApiClient) {
	return unwrap(api.POST("/api/v1/account/mfa/totp"));
}
