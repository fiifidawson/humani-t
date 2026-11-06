/**
 * Sign in again before a sensitive action (UI_UX_SPEC §6.1).
 *
 * The API decides which actions need a recent sign-in, and says so with a 401
 * carrying `reauthentication_required`. `withReauth` catches exactly that,
 * asks for the password in a modal over the current screen, and retries the
 * action once. The browser never guesses which actions are sensitive.
 */

import { Button, HStack, Stack, Text } from "@chakra-ui/react";
import {
	createContext,
	type FormEvent,
	type ReactNode,
	useCallback,
	useContext,
	useState,
} from "react";
import { unwrapEmpty } from "../../api/client";
import { isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { Dialog } from "../../ui/Dialog";
import { Field } from "../../ui/Field";

export class ReauthCancelledError extends Error {
	override name = "ReauthCancelledError";
}

type WithReauth = <T>(reason: string, action: () => Promise<T>) => Promise<T>;

interface PasswordRequest {
	reason: string;
	resolve: (isConfirmed: boolean) => void;
}

const ReauthContext = createContext<WithReauth | null>(null);

export function useReauth(): WithReauth {
	const withReauth = useContext(ReauthContext);
	if (withReauth === null) {
		throw new Error("useReauth must be used inside <ReauthProvider>");
	}
	return withReauth;
}

export function ReauthProvider({ children }: { children: ReactNode }) {
	const [request, setRequest] = useState<PasswordRequest | null>(null);

	const withReauth = useCallback<WithReauth>(async (reason, action) => {
		try {
			return await action();
		} catch (error) {
			if (!isApiError(error, "reauthentication-required")) {
				throw error;
			}
			const isConfirmed = await new Promise<boolean>((resolve) =>
				setRequest({ reason, resolve }),
			);
			if (!isConfirmed) {
				throw new ReauthCancelledError(reason);
			}
			return action();
		}
	}, []);

	const finish = (isConfirmed: boolean) => {
		request?.resolve(isConfirmed);
		setRequest(null);
	};

	return (
		<ReauthContext value={withReauth}>
			{children}
			<Dialog
				isOpen={request !== null}
				title="Sign in again"
				onClose={() => finish(false)}
			>
				{request === null ? null : (
					<PasswordForm
						reason={request.reason}
						onConfirmed={() => finish(true)}
						onCancel={() => finish(false)}
					/>
				)}
			</Dialog>
		</ReauthContext>
	);
}

interface PasswordFormProps {
	reason: string;
	onConfirmed: () => void;
	onCancel: () => void;
}

function PasswordForm({ reason, onConfirmed, onCancel }: PasswordFormProps) {
	const api = useApi();
	const [password, setPassword] = useState("");
	const [error, setError] = useState<string | undefined>(undefined);
	const [isSending, setSending] = useState(false);

	// A plain request, not a TanStack mutation: a wrong password answers 401,
	// and the app-wide watcher would read a mutation's 401 as "signed out".
	const onSubmit = async (event: FormEvent) => {
		event.preventDefault();
		setSending(true);
		setError(undefined);
		try {
			await unwrapEmpty(
				api.POST("/api/v1/auth/reauthenticate", { body: { password } }),
			);
			onConfirmed();
		} catch (failure) {
			setError(
				isApiError(failure)
					? userMessage(failure)
					: "That didn't work. Try again.",
			);
			setSending(false);
		}
	};

	return (
		<Stack as="form" gap="5" onSubmit={onSubmit}>
			<Text>{reason}</Text>
			<Field
				label="Password"
				type="password"
				autoComplete="current-password"
				required
				value={password}
				onChange={(event) => setPassword(event.target.value)}
				error={error}
			/>
			<HStack gap="3" wrap="wrap">
				<Button type="submit" disabled={isSending}>
					Confirm
				</Button>
				<Button type="button" variant="outline" onClick={onCancel}>
					Cancel
				</Button>
			</HStack>
		</Stack>
	);
}
