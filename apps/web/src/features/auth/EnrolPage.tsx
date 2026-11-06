/**
 * First sign-in: add a second factor, then keep the recovery codes
 * (UI_UX_SPEC §6.1). Until this is done the API lets the account reach
 * nothing else, so this page is the only way forward.
 */

import { Button, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { CircleX } from "lucide-react";
import { useState } from "react";
import { unwrapEmpty } from "../../api/client";
import { isApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { LinkButton } from "../../ui/LinkButton";
import { PageHeading } from "../../ui/PageHeading";
import { describeDevice } from "../account/describeDevice";
import { AuthLayout } from "./AuthLayout";
import { registerPasskey, startTotp } from "./authenticators";
import { RecoveryCodes } from "./RecoveryCodes";
import { TotpSetup } from "./TotpSetup";
import { isCancelled, isPasskeySupported } from "./webauthn";

type TotpEnrolment = components["schemas"]["TotpEnrolmentOut"];

type Step =
	| { kind: "choose" }
	| { kind: "totp"; enrolment: TotpEnrolment }
	| { kind: "codes"; codes: string[] };

function failureMessage(failure: unknown): string | null {
	if (failure === null) {
		return null;
	}
	if (isCancelled(failure)) {
		return "The passkey wasn't made. Try again, or use an authenticator app.";
	}
	return isApiError(failure)
		? userMessage(failure)
		: "This device couldn't make a passkey. Use an authenticator app instead.";
}

export function EnrolPage() {
	const api = useApi();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [step, setStep] = useState<Step>({ kind: "choose" });
	const [passkeyName, setPasskeyName] = useState(() =>
		describeDevice(navigator.userAgent),
	);

	const finish = async () => {
		// The session is now at full strength under a new token; forget the old view of it.
		queryClient.removeQueries({ queryKey: keys.session });
		await navigate({ to: "/" });
	};

	const afterFirstMethod = (codes: string[] | null) => {
		if (codes === null) {
			void finish();
		} else {
			setStep({ kind: "codes", codes });
		}
	};

	const passkey = useMutation({
		mutationFn: () => registerPasskey(api, passkeyName.trim() || "Passkey"),
		onSuccess: (answer) => afterFirstMethod(answer.recovery_codes),
	});
	const totp = useMutation({
		mutationFn: () => startTotp(api),
		onSuccess: (enrolment) => setStep({ kind: "totp", enrolment }),
	});
	const signOut = useMutation({
		mutationFn: () => unwrapEmpty(api.POST("/api/v1/auth/logout")),
		onSettled: async () => {
			queryClient.clear();
			await navigate({ to: "/sign-in" });
		},
	});

	const message = failureMessage(passkey.error ?? totp.error);

	return (
		<AuthLayout>
			<Stack gap="6">
				<PageHeading title="Protect your account" size="auth" />
				{step.kind === "choose" ? (
					<Stack gap="5">
						<Text>
							Humani-T needs a second way to confirm it's you. A passkey is the
							safest: it uses your fingerprint, face or a security key, and
							can't be phished.
						</Text>
						{message === null ? null : (
							<FormError role="alert" icon={<CircleX size={16} />}>
								{message}
							</FormError>
						)}
						{isPasskeySupported() ? (
							<>
								<Field
									label="Name this passkey"
									hint="So you can tell your passkeys apart later."
									value={passkeyName}
									onChange={(event) => setPasskeyName(event.target.value)}
								/>
								<Button
									type="button"
									size="lg"
									w="full"
									disabled={passkey.isPending}
									onClick={() => passkey.mutate()}
								>
									Add a passkey
								</Button>
							</>
						) : (
							<Text fontSize="sm" color="fg.muted">
								This browser can't make passkeys. Use an authenticator app.
							</Text>
						)}
						<Button
							type="button"
							variant="outline"
							size="lg"
							w="full"
							disabled={totp.isPending}
							onClick={() => totp.mutate()}
						>
							Use an authenticator app instead
						</Button>
						<LinkButton type="button" onClick={() => signOut.mutate()}>
							Sign out
						</LinkButton>
					</Stack>
				) : null}
				{step.kind === "totp" ? (
					<TotpSetup
						enrolment={step.enrolment}
						onConfirmed={afterFirstMethod}
						onCancel={() => setStep({ kind: "choose" })}
					/>
				) : null}
				{step.kind === "codes" ? (
					<RecoveryCodes codes={step.codes} onDone={() => void finish()} />
				) : null}
			</Stack>
		</AuthLayout>
	);
}
