/**
 * Sign in: the password, then the second factor.
 *
 * Every rule (which account exists, which factor is needed, how many tries)
 * is the API's. This screen sends what was typed and follows the answer.
 */

import { Button, Stack, Text, VisuallyHidden } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CircleX, Clock } from "lucide-react";
import { type FormEvent, useState } from "react";
import { unwrap } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { useSignedOutNotice } from "../../app/memory";
import { keys } from "../../app/queries";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { PageHeading } from "../../ui/PageHeading";
import { useCountdown } from "../../ui/useCountdown";
import { AuthLayout } from "./AuthLayout";
import { safeRedirect } from "./redirect";
import { SecondFactor } from "./SecondFactor";
import {
	type MfaMethod,
	readSignIn,
	type SignInOutcome,
} from "./signInOutcome";

const EVERY_METHOD: readonly MfaMethod[] = ["passkey", "totp", "recovery_code"];
const NO_MATCH = "That email and password don't match.";

type Step =
	| { kind: "password" }
	| { kind: "second-factor"; methods: readonly MfaMethod[] };

export function SignInPage() {
	const search = useSearch({ from: "/sign-in" });
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [, setSignedOut] = useSignedOutNotice();
	// After a reload part-way through, the API still holds the pending session
	// but the methods came only with the password answer, so offer them all.
	const [step, setStep] = useState<Step>(
		search.step === "second-factor"
			? { kind: "second-factor", methods: EVERY_METHOD }
			: { kind: "password" },
	);

	const leaveSignIn = async (to: "redirect" | "enrol") => {
		setSignedOut(false);
		queryClient.removeQueries({ queryKey: keys.session });
		await (to === "enrol"
			? navigate({ to: "/enrol" })
			: navigate({ href: safeRedirect(search.redirect) ?? "/" }));
	};

	const follow = (outcome: SignInOutcome) => {
		switch (outcome.status) {
			case "signed_in":
				return leaveSignIn("redirect");
			case "mfa_enrolment_required":
				return leaveSignIn("enrol");
			case "mfa_required":
				setStep({ kind: "second-factor", methods: outcome.methods });
				return;
		}
	};

	return (
		<AuthLayout>
			{step.kind === "password" ? (
				<PasswordStep onOutcome={follow} />
			) : (
				<SecondFactor
					methods={step.methods}
					onOutcome={follow}
					onStartAgain={() => setStep({ kind: "password" })}
				/>
			)}
		</AuthLayout>
	);
}

function PasswordStep({
	onOutcome,
}: {
	onOutcome: (outcome: SignInOutcome) => void;
}) {
	const api = useApi();
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [throttle, setThrottle] = useState<{
		until: number;
		seconds: number;
	} | null>(null);
	const secondsLeft = useCountdown(throttle?.until ?? null);

	const signIn = useMutation({
		mutationFn: async () =>
			readSignIn(
				await unwrap(
					api.POST("/api/v1/auth/login", { body: { email, password } }),
				),
			),
		onSuccess: onOutcome,
		onError: (error) => {
			if (isApiError(error, "throttled") && error.retryAfterSeconds !== null) {
				const seconds = error.retryAfterSeconds;
				setThrottle({ until: Date.now() + seconds * 1000, seconds });
			}
		},
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		signIn.mutate();
	};

	const isThrottled = secondsLeft > 0;
	const error = signIn.error;
	let message: string | null = null;
	if (isApiError(error, "signed-out")) {
		message = NO_MATCH;
	} else if (isApiError(error, "invalid")) {
		message = fieldErrors(error)["email"] ?? userMessage(error);
	} else if (isApiError(error) && !isApiError(error, "throttled")) {
		message = userMessage(error);
	}

	return (
		<Stack asChild gap="5">
			<form onSubmit={onSubmit} noValidate>
				<Stack gap="2">
					<PageHeading title="Sign in" size="auth" />
					<Text fontSize="sm" color="fg.muted">
						Evidence for humanitarian decisions.
					</Text>
				</Stack>
				{message === null ? null : (
					<FormError role="alert" icon={<CircleX size={16} />}>
						{message}
					</FormError>
				)}
				{isThrottled ? (
					<>
						<FormError icon={<Clock size={16} />}>
							{`Too many attempts. Try again in ${secondsLeft} seconds.`}
						</FormError>
						{/* Announced once; the visible countdown is not live, or it would speak every second. */}
						<VisuallyHidden role="alert">
							{`Too many sign-in attempts. Wait ${throttle?.seconds ?? secondsLeft} seconds before trying again.`}
						</VisuallyHidden>
					</>
				) : null}
				<Field
					label="Email"
					type="email"
					autoComplete="username"
					required
					value={email}
					onChange={(event) => setEmail(event.target.value)}
				/>
				<Field
					label="Password"
					type="password"
					autoComplete="current-password"
					required
					value={password}
					onChange={(event) => setPassword(event.target.value)}
				/>
				<Button
					type="submit"
					size="lg"
					w="full"
					disabled={isThrottled || signIn.isPending}
				>
					Sign in
				</Button>
			</form>
		</Stack>
	);
}
