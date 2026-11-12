/**
 * Words for the API's password answers. The API decides whether a password is
 * acceptable and says why not with a code; this file only words the codes.
 */

import { fieldErrors, isApiError } from "../../api/problem";

export const PASSWORD_HINT =
	"15 characters or more. A few unrelated words are easy to remember and hard to guess.";

const VIOLATION_WORDS: Readonly<Record<string, string>> = {
	too_short: "Use 15 characters or more.",
	too_long: "Use 1,024 characters or fewer.",
	too_common: "This password is too common. Choose one that's harder to guess.",
	contains_personal_details: "Don't use your name or email address in it.",
};

/** What to show under the password field, or `undefined` when the field is not at fault. */
export function passwordFieldError(
	error: unknown,
	field: "password" | "new_password",
): string | undefined {
	if (!isApiError(error, "invalid")) {
		return undefined;
	}
	const violations = error.problem.violations ?? [];
	if (violations.length > 0) {
		return violations
			.map((code) => VIOLATION_WORDS[code] ?? error.problem.detail ?? code)
			.join(" ");
	}
	return fieldErrors(error)[field];
}

/** An unknown, used or expired link: the API answers 400 and will not say which. */
export function isUnusableLink(error: unknown): boolean {
	return isApiError(error, "invalid") && error.status === 400;
}
