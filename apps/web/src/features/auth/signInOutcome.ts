/**
 * The API's sign-in answer as a union the screens can switch on.
 *
 * The generated `SignedInOut` is flat; narrowing it once here stops a screen
 * reading `user` on an answer that has none.
 */

import type { components } from "../../api/schema.gen";

type SignedInOut = components["schemas"]["SignedInOut"];
type User = components["schemas"]["UserOut"];

export type MfaMethod = "passkey" | "totp" | "recovery_code";

export type SignInOutcome =
	| { status: "signed_in"; user: User }
	| { status: "mfa_required"; methods: MfaMethod[] }
	| { status: "mfa_enrolment_required"; user: User };

const METHODS: readonly MfaMethod[] = ["passkey", "totp", "recovery_code"];

export class UnexpectedAnswerError extends Error {
	override name = "UnexpectedAnswerError";
}

export function readSignIn(answer: SignedInOut): SignInOutcome {
	if (answer.status === "mfa_required") {
		// A method this screen does not know is dropped, not guessed at.
		const methods = (answer.methods ?? []).flatMap((method) =>
			METHODS.filter((known) => known === method),
		);
		return { status: "mfa_required", methods };
	}
	if (answer.user === undefined || answer.user === null) {
		throw new UnexpectedAnswerError(
			`The API answered ${answer.status} without the account.`,
		);
	}
	return { status: answer.status, user: answer.user };
}
