/**
 * Forgotten password: ask for a link by email, then set a new password from it.
 * Opened without a token, the page asks for the link; with one, it sets the password.
 * A reset changes only the password: the second factor is still needed to sign in.
 */

import { Button, Link as ChakraLink, Stack, Text } from "@chakra-ui/react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleCheck, CircleX } from "lucide-react";
import { type FormEvent, useState } from "react";
import { unwrap, unwrapEmpty } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { LinkButton, linkStyle } from "../../ui/LinkButton";
import { PageHeading } from "../../ui/PageHeading";
import { AuthLayout } from "./AuthLayout";
import { useLinkToken } from "./linkToken";
import {
	isUnusableLink,
	PASSWORD_HINT,
	passwordFieldError,
} from "./passwordWords";

const UNUSABLE =
	"This reset link has expired or was already used. Ask for a new one.";

export function ResetPasswordPage() {
	const linkToken = useLinkToken();
	const [isAskingAgain, setIsAskingAgain] = useState(false);
	const token = isAskingAgain ? null : linkToken;

	return (
		<AuthLayout>
			{token === null ? (
				<RequestLink />
			) : (
				<NewPassword token={token} onAskAgain={() => setIsAskingAgain(true)} />
			)}
		</AuthLayout>
	);
}

function BackToSignIn({ label }: { label: string }) {
	return (
		<ChakraLink asChild css={linkStyle} alignSelf="flex-start">
			<Link to="/sign-in">{label}</Link>
		</ChakraLink>
	);
}

function RequestLink() {
	const api = useApi();
	const [email, setEmail] = useState("");
	const request = useMutation({
		mutationFn: async () =>
			unwrap(api.POST("/api/v1/auth/password-reset", { body: { email } })),
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		request.mutate();
	};

	const error = request.error;
	const emailError = isApiError(error, "invalid")
		? fieldErrors(error)["email"]
		: undefined;
	const message =
		isApiError(error) && emailError === undefined ? userMessage(error) : null;

	return (
		<Stack asChild gap="5">
			<form onSubmit={onSubmit} noValidate>
				<Stack gap="2">
					<PageHeading title="Reset your password" size="auth" />
					<Text fontSize="sm" color="fg.muted">
						We'll email you a link to set a new password.
					</Text>
				</Stack>
				{message === null ? null : (
					<FormError role="alert" icon={<CircleX size={16} />}>
						{message}
					</FormError>
				)}
				{request.isSuccess ? (
					<Text role="status" display="flex" gap="2" alignItems="center">
						<CircleCheck size={16} aria-hidden="true" />
						If an account uses that email, a reset link is on its way. It works
						once, for 30 minutes.
					</Text>
				) : (
					<>
						<Field
							label="Email"
							type="email"
							autoComplete="username"
							required
							value={email}
							error={emailError}
							onChange={(event) => setEmail(event.target.value)}
						/>
						<Button
							type="submit"
							size="lg"
							w="full"
							disabled={request.isPending}
						>
							Send reset link
						</Button>
					</>
				)}
				<BackToSignIn label="Back to sign in" />
			</form>
		</Stack>
	);
}

function NewPassword({
	token,
	onAskAgain,
}: {
	token: string;
	onAskAgain: () => void;
}) {
	const api = useApi();
	const [password, setPassword] = useState("");
	const confirm = useMutation({
		mutationFn: async () =>
			unwrapEmpty(
				api.POST("/api/v1/auth/password-reset/confirm", {
					body: { token, new_password: password },
				}),
			),
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		confirm.mutate();
	};

	if (confirm.isSuccess) {
		return (
			<Stack gap="5">
				<PageHeading title="Password changed" size="auth" />
				<Text role="status">
					Your password is changed. You'll still need your passkey or
					authenticator code to sign in.
				</Text>
				<Button asChild size="lg" w="full">
					<Link to="/sign-in">Sign in</Link>
				</Button>
			</Stack>
		);
	}

	const error = confirm.error;
	const isUnusable = isUnusableLink(error);
	const passwordError = passwordFieldError(error, "new_password");
	let message: string | null = null;
	if (isUnusable) {
		message = UNUSABLE;
	} else if (isApiError(error) && passwordError === undefined) {
		message = userMessage(error);
	}

	return (
		<Stack asChild gap="5">
			<form onSubmit={onSubmit} noValidate>
				<PageHeading title="Set a new password" size="auth" />
				{message === null ? null : (
					<FormError role="alert" icon={<CircleX size={16} />}>
						{message}
					</FormError>
				)}
				{isUnusable ? (
					<LinkButton type="button" onClick={onAskAgain}>
						Ask for a new link
					</LinkButton>
				) : null}
				<Field
					label="New password"
					type="password"
					autoComplete="new-password"
					required
					hint={PASSWORD_HINT}
					value={password}
					error={passwordError}
					onChange={(event) => setPassword(event.target.value)}
				/>
				<Button type="submit" size="lg" w="full" disabled={confirm.isPending}>
					Set new password
				</Button>
			</form>
		</Stack>
	);
}
