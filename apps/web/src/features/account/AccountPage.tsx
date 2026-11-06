/**
 * The signed-in person's own account (UI_UX_SPEC §6.16): where they are
 * signed in, how they sign in, and how the app looks. Password change and
 * text size are not here yet.
 */

import {
	Badge,
	Button,
	chakra,
	Flex,
	HStack,
	RadioGroup,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleX } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { unwrap, unwrapEmpty } from "../../api/client";
import { isApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { type ThemeChoice, useTheme } from "../../app/preferences";
import { devicesQuery, keys, mfaQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { FormError } from "../../ui/FormError";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { SectionHeading } from "../../ui/SectionHeading";
import { registerPasskey, startTotp } from "../auth/authenticators";
import { ReauthCancelledError, useReauth } from "../auth/ReauthDialog";
import { RecoveryCodes } from "../auth/RecoveryCodes";
import { TotpSetup } from "../auth/TotpSetup";
import { isCancelled, isPasskeySupported } from "../auth/webauthn";
import { fullDate, shortDate } from "../projects/format";
import { describeDevice } from "./describeDevice";

type TotpEnrolment = components["schemas"]["TotpEnrolmentOut"];

/** A list of things the person has, each a card-like row with its action on the right. */
const Rows = chakra("ul", {
	base: {
		listStyle: "none",
		m: "0",
		p: "0",
		rounded: "card",
		borderWidth: "1px",
		borderColor: "border",
		bg: "bg.panel",
		"& > li + li": { borderTopWidth: "1px", borderColor: "border" },
	},
});

const Row = chakra("li", {
	base: {
		display: "flex",
		flexWrap: "wrap",
		alignItems: "center",
		justifyContent: "space-between",
		gap: "3",
		px: "4",
		py: "3",
	},
});

const RowMain = chakra("div", {
	base: { display: "flex", flexDirection: "column", gap: "1", minW: "0" },
});

function Section({ title, children }: { title: string; children: ReactNode }) {
	const headingId = useId();
	return (
		<section aria-labelledby={headingId}>
			<SectionHeading id={headingId}>{title}</SectionHeading>
			<Stack gap="4">{children}</Stack>
		</section>
	);
}

function DevicesSection() {
	const api = useApi();
	const queryClient = useQueryClient();
	const devices = useQuery(devicesQuery(api));
	const refresh = () =>
		queryClient.invalidateQueries({ queryKey: keys.devices });

	const signOutOne = useMutation({
		mutationFn: (sessionId: string) =>
			unwrapEmpty(
				api.DELETE("/api/v1/account/sessions/{session_id}", {
					params: { path: { session_id: sessionId } },
				}),
			),
		onSuccess: refresh,
	});
	const signOutOthers = useMutation({
		mutationFn: () =>
			unwrapEmpty(api.POST("/api/v1/account/sessions/revoke-others")),
		onSuccess: refresh,
	});

	if (devices.isPending) {
		return <Loading what="devices" />;
	}
	if (devices.isError) {
		return (
			<ErrorNotice
				error={devices.error}
				onRetry={() => void devices.refetch()}
			/>
		);
	}
	const ordered = [...devices.data].sort(
		(a, b) => Number(b.current) - Number(a.current),
	);
	const failure = signOutOne.error ?? signOutOthers.error;

	return (
		<>
			{failure === null ? null : <ErrorNotice error={failure} />}
			<Rows>
				{ordered.map((device) => {
					const label = describeDevice(device.user_agent);
					return (
						<Row key={device.id}>
							<RowMain>
								<Text as="span" fontWeight="medium">
									{label}
								</Text>
								{device.current ? (
									<Badge
										variant="outline"
										colorPalette="gray"
										alignSelf="flex-start"
									>
										This device
									</Badge>
								) : null}
								<Text as="span" fontSize="sm" color="fg.muted">
									Last active{" "}
									<time
										dateTime={device.last_seen_at}
										title={fullDate(device.last_seen_at)}
									>
										{shortDate(device.last_seen_at)}
									</time>
								</Text>
							</RowMain>
							{device.current ? null : (
								<Button
									type="button"
									variant="outline"
									size="sm"
									aria-label={`Sign out ${label}`}
									disabled={signOutOne.isPending}
									onClick={() => signOutOne.mutate(device.id)}
								>
									Sign out
								</Button>
							)}
						</Row>
					);
				})}
			</Rows>
			{ordered.some((device) => !device.current) ? (
				<Button
					type="button"
					variant="outline"
					size="sm"
					disabled={signOutOthers.isPending}
					onClick={() => signOutOthers.mutate()}
				>
					Sign out all other devices
				</Button>
			) : null}
		</>
	);
}

function failureMessage(failure: unknown): string {
	if (isCancelled(failure)) {
		return "The passkey wasn't made.";
	}
	return isApiError(failure)
		? userMessage(failure)
		: "That didn't work. Try again.";
}

type Shown =
	| { kind: "list" }
	| { kind: "totp"; enrolment: TotpEnrolment }
	| { kind: "codes"; codes: string[] };

function MethodsSection() {
	const api = useApi();
	const queryClient = useQueryClient();
	const withReauth = useReauth();
	const mfa = useQuery(mfaQuery(api));
	const [shown, setShown] = useState<Shown>({ kind: "list" });
	const [failure, setFailure] = useState<unknown>(null);
	const [passkeyName, setPasskeyName] = useState("");
	const refresh = () => queryClient.invalidateQueries({ queryKey: keys.mfa });

	// Every action here shares one place to report a failure. A cancelled
	// re-authentication is the person changing their mind, not a failure.
	const reporting = {
		onMutate: () => setFailure(null),
		onError: (error: Error) => {
			if (!(error instanceof ReauthCancelledError)) {
				setFailure(error);
			}
		},
	};

	const removePasskey = useMutation({
		...reporting,
		mutationFn: (passkeyId: string) =>
			withReauth("Removing a passkey needs you to sign in again.", () =>
				unwrapEmpty(
					api.DELETE("/api/v1/account/mfa/passkeys/{passkey_id}", {
						params: { path: { passkey_id: passkeyId } },
					}),
				),
			),
		onSuccess: refresh,
	});
	const removeTotp = useMutation({
		...reporting,
		mutationFn: () =>
			withReauth(
				"Removing the authenticator app needs you to sign in again.",
				() => unwrapEmpty(api.DELETE("/api/v1/account/mfa/totp")),
			),
		onSuccess: refresh,
	});
	const addPasskey = useMutation({
		...reporting,
		mutationFn: () =>
			withReauth("Adding a passkey needs you to sign in again.", () =>
				registerPasskey(
					api,
					passkeyName.trim() || describeDevice(navigator.userAgent),
				),
			),
		onSuccess: async () => {
			setPasskeyName("");
			await refresh();
		},
	});
	const addTotp = useMutation({
		...reporting,
		mutationFn: () =>
			withReauth(
				"Setting up an authenticator app needs you to sign in again.",
				() => startTotp(api),
			),
		onSuccess: (enrolment) => setShown({ kind: "totp", enrolment }),
	});
	const newCodes = useMutation({
		...reporting,
		mutationFn: () =>
			withReauth("Making new recovery codes needs you to sign in again.", () =>
				unwrap(api.POST("/api/v1/account/mfa/recovery-codes")),
			),
		onSuccess: async (answer) => {
			setShown({ kind: "codes", codes: answer.recovery_codes });
			await refresh();
		},
	});

	if (shown.kind === "totp") {
		return (
			<TotpSetup
				enrolment={shown.enrolment}
				onConfirmed={async () => {
					setShown({ kind: "list" });
					await refresh();
				}}
				onCancel={() => setShown({ kind: "list" })}
			/>
		);
	}
	if (shown.kind === "codes") {
		return (
			<RecoveryCodes
				codes={shown.codes}
				onDone={() => setShown({ kind: "list" })}
			/>
		);
	}
	if (mfa.isPending) {
		return <Loading what="sign-in methods" />;
	}
	if (mfa.isError) {
		return <ErrorNotice error={mfa.error} onRetry={() => void mfa.refetch()} />;
	}

	const remaining = mfa.data.recovery_codes_remaining;
	const onAddPasskey = (event: FormEvent) => {
		event.preventDefault();
		addPasskey.mutate();
	};

	return (
		<>
			{failure === null ? null : (
				<FormError role="alert" icon={<CircleX size={16} />}>
					{failureMessage(failure)}
				</FormError>
			)}
			<Rows>
				{mfa.data.passkeys.map((passkey) => (
					<Row key={passkey.id}>
						<RowMain>
							<Text as="span" fontWeight="medium">
								{passkey.name}
							</Text>
							<Text as="span" fontSize="sm" color="fg.muted">
								Passkey · added{" "}
								<time
									dateTime={passkey.created_at}
									title={fullDate(passkey.created_at)}
								>
									{shortDate(passkey.created_at)}
								</time>
							</Text>
						</RowMain>
						<Button
							type="button"
							variant="outline"
							size="sm"
							aria-label={`Remove ${passkey.name}`}
							disabled={removePasskey.isPending}
							onClick={() => removePasskey.mutate(passkey.id)}
						>
							Remove
						</Button>
					</Row>
				))}
				<Row>
					<RowMain>
						<Text as="span" fontWeight="medium">
							Authenticator app
						</Text>
						<Text as="span" fontSize="sm" color="fg.muted">
							{mfa.data.totp === null ? "Not set up" : "Set up"}
						</Text>
					</RowMain>
					{mfa.data.totp === null ? (
						<Button
							type="button"
							variant="outline"
							size="sm"
							disabled={addTotp.isPending}
							onClick={() => addTotp.mutate()}
						>
							Set up
						</Button>
					) : (
						<Button
							type="button"
							variant="outline"
							size="sm"
							aria-label="Remove the authenticator app"
							disabled={removeTotp.isPending}
							onClick={() => removeTotp.mutate()}
						>
							Remove
						</Button>
					)}
				</Row>
				<Row>
					<RowMain>
						<Text as="span" fontWeight="medium">
							Recovery codes
						</Text>
						<Text as="span" fontSize="sm" color="fg.muted">
							{remaining === 1
								? "1 recovery code left"
								: `${remaining} recovery codes left`}
						</Text>
					</RowMain>
					<Button
						type="button"
						variant="outline"
						size="sm"
						disabled={newCodes.isPending}
						onClick={() => newCodes.mutate()}
					>
						Make new recovery codes
					</Button>
				</Row>
			</Rows>
			{isPasskeySupported() ? (
				<Flex
					as="form"
					onSubmit={onAddPasskey}
					gap="3"
					align={{ base: "stretch", sm: "flex-end" }}
					direction={{ base: "column", sm: "row" }}
					css={{ "& > :first-of-type": { flex: "1" } }}
				>
					<Field
						label="Name for the new passkey"
						placeholder={describeDevice(navigator.userAgent)}
						value={passkeyName}
						onChange={(event) => setPasskeyName(event.target.value)}
					/>
					<Button type="submit" disabled={addPasskey.isPending}>
						Add a passkey
					</Button>
				</Flex>
			) : (
				<Text fontSize="sm" color="fg.muted">
					This browser can't make passkeys.
				</Text>
			)}
		</>
	);
}

const THEMES: readonly { value: ThemeChoice; label: string }[] = [
	{ value: "system", label: "System" },
	{ value: "light", label: "Light" },
	{ value: "dark", label: "Dark" },
];

function AppearanceSection() {
	const [theme, setTheme] = useTheme();
	const legendId = useId();
	return (
		<RadioGroup.Root
			value={theme}
			onValueChange={(details) => {
				const choice = THEMES.find((option) => option.value === details.value);
				if (choice !== undefined) {
					setTheme(choice.value);
				}
			}}
			aria-labelledby={legendId}
		>
			<Text id={legendId} fontWeight="medium" mb="3">
				Theme
			</Text>
			<HStack gap="6" wrap="wrap">
				{THEMES.map((option) => (
					<RadioGroup.Item key={option.value} value={option.value}>
						<RadioGroup.ItemHiddenInput />
						<RadioGroup.ItemIndicator />
						<RadioGroup.ItemText>{option.label}</RadioGroup.ItemText>
					</RadioGroup.Item>
				))}
			</HStack>
		</RadioGroup.Root>
	);
}

export function AccountPage() {
	return (
		<Page isNarrow>
			<PageHeading title="Account" />
			<Section title="Signed-in devices">
				<DevicesSection />
			</Section>
			<Section title="Sign-in methods">
				<MethodsSection />
			</Section>
			<Section title="Appearance">
				<AppearanceSection />
			</Section>
		</Page>
	);
}
