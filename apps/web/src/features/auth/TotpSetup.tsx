import { Button, Code, chakra, HStack, Stack, Text } from "@chakra-ui/react";
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { unwrap } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { Field } from "../../ui/Field";
import { linkStyle } from "../../ui/LinkButton";
import { SectionHeading } from "../../ui/SectionHeading";
import { QrCode } from "./QrCode";

type TotpEnrolment = components["schemas"]["TotpEnrolmentOut"];

interface TotpSetupProps {
	enrolment: TotpEnrolment;
	/** The recovery codes when this was the first sign-in method, otherwise `null`. */
	onConfirmed: (recoveryCodes: string[] | null) => void;
	onCancel: () => void;
}

/** Groups of four, as authenticator apps show keys, so it can be typed without losing place. */
const grouped = (secret: string) =>
	secret.match(/.{1,4}/g)?.join(" ") ?? secret;

/** Scan or type the key the API issued, then prove it with a first code. */
export function TotpSetup({
	enrolment,
	onConfirmed,
	onCancel,
}: TotpSetupProps) {
	const api = useApi();
	const [code, setCode] = useState("");
	const confirm = useMutation({
		mutationFn: () =>
			unwrap(
				api.POST("/api/v1/account/mfa/totp/confirm", {
					body: { code: code.replace(/\s+/g, "") },
				}),
			),
		onSuccess: (answer) => onConfirmed(answer.recovery_codes),
	});

	const onSubmit = (event: FormEvent) => {
		event.preventDefault();
		confirm.mutate();
	};

	const error = isApiError(confirm.error)
		? (fieldErrors(confirm.error)["code"] ?? userMessage(confirm.error))
		: undefined;

	return (
		<Stack as="form" gap="5" onSubmit={onSubmit}>
			<SectionHeading>Set up your authenticator app</SectionHeading>
			<Text>Open your authenticator app and scan this code.</Text>
			<QrCode
				value={enrolment.otpauth_uri}
				label="QR code for your authenticator app"
			/>
			<Text fontSize="sm">
				Or type this key:{" "}
				<Code fontSize="sm" px="1.5" py="0.5" letterSpacing="0.04em">
					{grouped(enrolment.secret)}
				</Code>
			</Text>
			<Text fontSize="sm">
				<chakra.a href={enrolment.otpauth_uri} css={linkStyle}>
					Open in an authenticator app on this device
				</chakra.a>
			</Text>
			<Field
				label="6-digit code from your authenticator app"
				inputMode="numeric"
				autoComplete="one-time-code"
				required
				value={code}
				error={error}
				onChange={(event) => setCode(event.target.value)}
			/>
			<HStack gap="3" wrap="wrap">
				<Button type="submit" disabled={confirm.isPending}>
					Confirm
				</Button>
				<Button type="button" variant="outline" onClick={onCancel}>
					Cancel
				</Button>
			</HStack>
		</Stack>
	);
}
