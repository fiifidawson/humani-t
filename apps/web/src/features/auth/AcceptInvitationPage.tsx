/**
 * First sign-in from an invitation (UI_UX_SPEC §6.1): a name and a password,
 * then on to whatever the API says comes next, usually adding a second factor.
 */

import { Button, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { CircleX } from "lucide-react";
import { type FormEvent, useState } from "react";
import { unwrap } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { PageHeading } from "../../ui/PageHeading";
import { AuthLayout } from "./AuthLayout";
import { useLinkToken } from "./linkToken";
import {
	isUnusableLink,
	PASSWORD_HINT,
	passwordFieldError,
} from "./passwordWords";
import { readSignIn, type SignInOutcome } from "./signInOutcome";

const TITLE = "Set up your account";
const UNUSABLE =
	"This invitation link has expired or was already used. Ask your administrator for a new one.";

export function AcceptInvitationPage() {
	const token = useLinkToken();
	return (
		<AuthLayout>
			<Stack gap="5">
				<PageHeading title={TITLE} size="auth" />
				{token === null ? (
					<FormError icon={<CircleX size={16} />}>{UNUSABLE}</FormError>
				) : (
					<RegisterForm token={token} />
				)}
			</Stack>
		</AuthLayout>
	);
}

function RegisterForm({ token }: { token: string }) {
	const api = useApi();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [displayName, setDisplayName] = useState("");
	const [password, setPassword] = useState("");

	const follow = async (outcome: SignInOutcome) => {
		queryClient.removeQueries({ queryKey: keys.session });
		switch (outcome.status) {
			case "mfa_enrolment_required":
				return navigate({ to: "/enrol" });
			case "signed_in":
				return navigate({ to: "/" });
			case "mfa_required":
				return navigate({ to: "/sign-in", search: { step: "second-factor" } });
		}
	};

	const register = useMutation({
		mutationFn: async () =>
			readSignIn(
				await unwrap(
					api.POST("/api/v1/auth/register", {
						body: { token, display_name: displayName, password },
					}),
				),
			),
		onSuccess: follow,
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		register.mutate();
	};

	const error = register.error;
	const passwordError = passwordFieldError(error, "password");
	const nameError = isApiError(error, "invalid")
		? fieldErrors(error)["display_name"]
		: undefined;
	let message: string | null = null;
	if (isUnusableLink(error)) {
		message = UNUSABLE;
	} else if (
		isApiError(error) &&
		passwordError === undefined &&
		nameError === undefined
	) {
		message = userMessage(error);
	}

	return (
		<Stack asChild gap="5">
			<form onSubmit={onSubmit} noValidate>
				<Text fontSize="sm" color="fg.muted">
					Choose the name others in your projects will see, and a password.
				</Text>
				{message === null ? null : (
					<FormError role="alert" icon={<CircleX size={16} />}>
						{message}
					</FormError>
				)}
				<Field
					label="Your name"
					autoComplete="name"
					required
					value={displayName}
					error={nameError}
					onChange={(event) => setDisplayName(event.target.value)}
				/>
				<Field
					label="Password"
					type="password"
					autoComplete="new-password"
					required
					hint={PASSWORD_HINT}
					value={password}
					error={passwordError}
					onChange={(event) => setPassword(event.target.value)}
				/>
				<Button type="submit" size="lg" w="full" disabled={register.isPending}>
					Create account
				</Button>
			</form>
		</Stack>
	);
}
