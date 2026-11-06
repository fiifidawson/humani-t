/**
 * The second factor at sign-in: passkey, authenticator code or recovery code.
 *
 * The passkey prompt starts by itself when the account has one (UI_UX_SPEC
 * §6.1). No text-message codes are offered: they can be intercepted.
 */

import { Button, Stack, Text } from "@chakra-ui/react";
import { useMutation } from "@tanstack/react-query";
import { CircleX, KeyRound } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { unwrap } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { LinkButton } from "../../ui/LinkButton";
import { PageHeading } from "../../ui/PageHeading";
import {
	type MfaMethod,
	readSignIn,
	type SignInOutcome,
} from "./signInOutcome";
import { isCancelled, isPasskeySupported, signInWithPasskey } from "./webauthn";

interface SecondFactorProps {
	methods: readonly MfaMethod[];
	onOutcome: (outcome: SignInOutcome) => void;
	onStartAgain: () => void;
}

const SWITCH_LABELS: Readonly<Record<MfaMethod, string>> = {
	passkey: "Use a passkey instead",
	totp: "Use an authenticator code instead",
	recovery_code: "Use a recovery code",
};

function firstChoice(methods: readonly MfaMethod[]): MfaMethod {
	if (methods.includes("passkey") && isPasskeySupported()) {
		return "passkey";
	}
	return methods.includes("totp") ? "totp" : "recovery_code";
}

export function SecondFactor({
	methods,
	onOutcome,
	onStartAgain,
}: SecondFactorProps) {
	const [method, setMethod] = useState<MfaMethod>(() => firstChoice(methods));
	const others = methods.filter(
		(m) => m !== method && (m !== "passkey" || isPasskeySupported()),
	);

	return (
		<Stack gap="5">
			<PageHeading title="Confirm it's you" size="auth" />
			{method === "passkey" ? <PasskeyStep onOutcome={onOutcome} /> : null}
			{method === "totp" ? (
				<CodeStep
					key="totp"
					path="/api/v1/auth/mfa/totp"
					label="6-digit code from your authenticator app"
					inputMode="numeric"
					autoComplete="one-time-code"
					onOutcome={onOutcome}
				/>
			) : null}
			{method === "recovery_code" ? (
				<CodeStep
					key="recovery"
					path="/api/v1/auth/mfa/recovery-code"
					label="Recovery code"
					hint="Each recovery code works once."
					inputMode="text"
					autoComplete="off"
					onOutcome={onOutcome}
				/>
			) : null}
			<Stack gap="2.5" align="flex-start">
				{others.map((other) => (
					<LinkButton
						key={other}
						type="button"
						onClick={() => setMethod(other)}
					>
						{SWITCH_LABELS[other]}
					</LinkButton>
				))}
				<LinkButton type="button" onClick={onStartAgain}>
					Start again with a different account
				</LinkButton>
			</Stack>
			<Text
				fontSize="sm"
				color="fg.muted"
				borderTopWidth="1px"
				borderColor="border"
				pt="4"
			>
				No text-message codes: they can be intercepted.
			</Text>
		</Stack>
	);
}

function PasskeyStep({
	onOutcome,
}: {
	onOutcome: (outcome: SignInOutcome) => void;
}) {
	const api = useApi();
	const passkey = useMutation({
		mutationFn: async () => {
			const { options } = await unwrap(
				api.POST("/api/v1/auth/mfa/passkey/options"),
			);
			const credential = await signInWithPasskey(options);
			return readSignIn(
				await unwrap(
					api.POST("/api/v1/auth/mfa/passkey", { body: { credential } }),
				),
			);
		},
		onSuccess: onOutcome,
	});

	// Once per mount, even when React runs effects twice in development: two
	// prompts at once would make the browser cancel the first.
	const hasStarted = useRef(false);
	const { mutate } = passkey;
	useEffect(() => {
		if (!hasStarted.current) {
			hasStarted.current = true;
			mutate();
		}
	}, [mutate]);

	if (passkey.isError) {
		const message = isCancelled(passkey.error)
			? "The passkey wasn't used."
			: isApiError(passkey.error)
				? userMessage(passkey.error)
				: "The passkey couldn't be used on this device.";
		return (
			<Stack gap="4" align="flex-start">
				<FormError role="alert" icon={<CircleX size={16} />}>
					{message}
				</FormError>
				<Button type="button" onClick={() => passkey.mutate()}>
					Try the passkey again
				</Button>
			</Stack>
		);
	}

	return (
		<Stack
			role="status"
			gap="2"
			align="center"
			textAlign="center"
			p="6"
			rounded="card"
			bg="bg.subtle"
		>
			<KeyRound size={28} />
			<Text fontWeight="semibold">Waiting for your passkey…</Text>
			<Text fontSize="sm" color="fg.muted">
				Your browser asks for it. Use your fingerprint, face or security key.
			</Text>
		</Stack>
	);
}

interface CodeStepProps {
	path: "/api/v1/auth/mfa/totp" | "/api/v1/auth/mfa/recovery-code";
	label: string;
	hint?: string;
	inputMode: "numeric" | "text";
	autoComplete: string;
	onOutcome: (outcome: SignInOutcome) => void;
}

function CodeStep({
	path,
	label,
	hint,
	inputMode,
	autoComplete,
	onOutcome,
}: CodeStepProps) {
	const api = useApi();
	const [code, setCode] = useState("");
	const submit = useMutation({
		// Spaces are how people copy codes, not part of them.
		mutationFn: async () =>
			readSignIn(
				await unwrap(
					api.POST(path, { body: { code: code.replace(/\s+/g, "") } }),
				),
			),
		onSuccess: onOutcome,
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		submit.mutate();
	};

	const error = isApiError(submit.error)
		? (fieldErrors(submit.error)["code"] ?? userMessage(submit.error))
		: undefined;

	return (
		<Stack as="form" gap="5" onSubmit={onSubmit}>
			<Field
				label={label}
				hint={hint}
				error={error}
				inputMode={inputMode}
				autoComplete={autoComplete}
				autoCapitalize="characters"
				spellCheck={false}
				required
				value={code}
				onChange={(event) => setCode(event.target.value)}
			/>
			<Button type="submit" size="lg" w="full" disabled={submit.isPending}>
				Continue
			</Button>
		</Stack>
	);
}
