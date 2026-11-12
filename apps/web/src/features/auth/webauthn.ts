/**
 * Passkey ceremonies: convert between the API's WebAuthn JSON (base64url) and the
 * browser's ArrayBuffers. Whether a passkey is accepted is the API's decision.
 */

/** A new passkey as the API verifies it: the RegistrationResponseJSON fields py_webauthn reads. */
export type PasskeyRegistrationJSON = {
	id: string;
	rawId: string;
	type: "public-key";
	authenticatorAttachment?: string;
	clientExtensionResults: Record<string, never>;
	response: {
		clientDataJSON: string;
		attestationObject: string;
		transports: string[];
	};
};

/** A passkey's signature over the API's challenge: AuthenticationResponseJSON. */
export type PasskeyAssertionJSON = {
	id: string;
	rawId: string;
	type: "public-key";
	authenticatorAttachment?: string;
	clientExtensionResults: Record<string, never>;
	response: {
		clientDataJSON: string;
		authenticatorData: string;
		signature: string;
		userHandle?: string;
	};
};

export class MalformedOptionsError extends Error {
	override name = "MalformedOptionsError";
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === "object" && value !== null && !Array.isArray(value);

const isString = (value: unknown): value is string => typeof value === "string";

// The API types its options as a free-form object (they come from
// py_webauthn), so the fields the ceremony cannot do without are checked
// here. Optional fields are narrowed one by one in the converters below.
function readCreationOptions(
	value: unknown,
): PublicKeyCredentialCreationOptionsJSON {
	const isValid =
		isRecord(value) &&
		isString(value["challenge"]) &&
		isRecord(value["rp"]) &&
		isString(value["rp"]["name"]) &&
		isRecord(value["user"]) &&
		isString(value["user"]["id"]) &&
		isString(value["user"]["name"]) &&
		isString(value["user"]["displayName"]) &&
		Array.isArray(value["pubKeyCredParams"]);
	if (!isValid) {
		throw new MalformedOptionsError(
			"The passkey options from the server are incomplete.",
		);
	}
	return value as unknown as PublicKeyCredentialCreationOptionsJSON;
}

function readRequestOptions(
	value: unknown,
): PublicKeyCredentialRequestOptionsJSON {
	if (!isRecord(value) || !isString(value["challenge"])) {
		throw new MalformedOptionsError(
			"The passkey options from the server are incomplete.",
		);
	}
	return value as unknown as PublicKeyCredentialRequestOptionsJSON;
}

export function bufferToBase64url(buffer: ArrayBuffer): string {
	let binary = "";
	for (const byte of new Uint8Array(buffer)) {
		binary += String.fromCharCode(byte);
	}
	return btoa(binary)
		.replace(/\+/g, "-")
		.replace(/\//g, "_")
		.replace(/=+$/, "");
}

export function base64urlToBuffer(value: string): ArrayBuffer {
	const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
	const binary = atob(
		base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "="),
	);
	const bytes = new Uint8Array(binary.length);
	for (let index = 0; index < binary.length; index += 1) {
		bytes[index] = binary.charCodeAt(index);
	}
	return bytes.buffer;
}

function oneOf<T extends string>(
	allowed: readonly T[],
	value: string | undefined,
): T | undefined {
	return allowed.find((candidate) => candidate === value);
}

const TRANSPORTS = ["ble", "hybrid", "internal", "nfc", "usb"] as const;
const VERIFICATION = ["discouraged", "preferred", "required"] as const;
const RESIDENT_KEY = ["discouraged", "preferred", "required"] as const;
const ATTACHMENT = ["cross-platform", "platform"] as const;
const ATTESTATION = ["direct", "enterprise", "indirect", "none"] as const;

/** Copy only the keys whose value is defined: `exactOptionalPropertyTypes` forbids explicit undefined. */
function defined<T extends object>(
	entries: { [K in keyof T]?: T[K] | undefined },
): Partial<T> {
	return Object.fromEntries(
		Object.entries(entries).filter(([, value]) => value !== undefined),
	) as Partial<T>;
}

// Hand-written rather than PublicKeyCredential.parseCreationOptionsFromJSON:
// those helpers reached every major browser only in 2025, and field teams
// keep older devices. One tested code path works on both.
function descriptorsFromJSON(
	descriptors: PublicKeyCredentialDescriptorJSON[] | undefined,
): PublicKeyCredentialDescriptor[] | undefined {
	return descriptors?.map((descriptor) => ({
		type: "public-key",
		id: base64urlToBuffer(descriptor.id),
		...defined<{ transports: AuthenticatorTransport[] }>({
			transports: descriptor.transports?.flatMap(
				(t) => oneOf(TRANSPORTS, t) ?? [],
			),
		}),
	}));
}

function selectionFromJSON(
	selection: AuthenticatorSelectionCriteria | undefined,
): AuthenticatorSelectionCriteria | undefined {
	if (selection === undefined) {
		return undefined;
	}
	return defined<AuthenticatorSelectionCriteria>({
		authenticatorAttachment: oneOf(
			ATTACHMENT,
			selection.authenticatorAttachment,
		),
		residentKey: oneOf(RESIDENT_KEY, selection.residentKey),
		requireResidentKey: selection.requireResidentKey,
		userVerification: oneOf(VERIFICATION, selection.userVerification),
	});
}

function creationOptionsFromJSON(
	json: PublicKeyCredentialCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
	return {
		rp: json.rp,
		user: {
			id: base64urlToBuffer(json.user.id),
			name: json.user.name,
			displayName: json.user.displayName,
		},
		challenge: base64urlToBuffer(json.challenge),
		pubKeyCredParams: json.pubKeyCredParams.map(({ alg }) => ({
			type: "public-key",
			alg,
		})),
		...defined<PublicKeyCredentialCreationOptions>({
			timeout: json.timeout,
			excludeCredentials: descriptorsFromJSON(json.excludeCredentials),
			authenticatorSelection: selectionFromJSON(json.authenticatorSelection),
			attestation: oneOf(ATTESTATION, json.attestation),
		}),
	};
}

function requestOptionsFromJSON(
	json: PublicKeyCredentialRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
	return {
		challenge: base64urlToBuffer(json.challenge),
		...defined<PublicKeyCredentialRequestOptions>({
			rpId: json.rpId,
			timeout: json.timeout,
			allowCredentials: descriptorsFromJSON(json.allowCredentials),
			userVerification: oneOf(VERIFICATION, json.userVerification),
		}),
	};
}

function common(credential: PublicKeyCredential) {
	return {
		id: credential.id,
		rawId: bufferToBase64url(credential.rawId),
		type: "public-key" as const,
		...defined<{ authenticatorAttachment: string }>({
			authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
		}),
		// The API requests no extensions, so there are no results to send.
		clientExtensionResults: {},
	};
}

function registrationToJSON(
	credential: PublicKeyCredential,
): PasskeyRegistrationJSON {
	const response = credential.response as AuthenticatorAttestationResponse;
	return {
		...common(credential),
		response: {
			clientDataJSON: bufferToBase64url(response.clientDataJSON),
			attestationObject: bufferToBase64url(response.attestationObject),
			transports:
				typeof response.getTransports === "function"
					? response.getTransports()
					: [],
		},
	};
}

function assertionToJSON(
	credential: PublicKeyCredential,
): PasskeyAssertionJSON {
	const response = credential.response as AuthenticatorAssertionResponse;
	return {
		...common(credential),
		response: {
			clientDataJSON: bufferToBase64url(response.clientDataJSON),
			authenticatorData: bufferToBase64url(response.authenticatorData),
			signature: bufferToBase64url(response.signature),
			...defined<{ userHandle: string }>({
				userHandle:
					response.userHandle === null
						? undefined
						: bufferToBase64url(response.userHandle),
			}),
		},
	};
}

export class PasskeyUnavailableError extends Error {
	override name = "PasskeyUnavailableError";
}

async function ceremony(
	run: () => Promise<Credential | null>,
): Promise<PublicKeyCredential> {
	const credential = await run();
	if (credential === null) {
		throw new PasskeyUnavailableError("The browser returned no passkey.");
	}
	return credential as PublicKeyCredential;
}

export function isPasskeySupported(): boolean {
	return (
		typeof window.PublicKeyCredential !== "undefined" &&
		"credentials" in navigator
	);
}

/** Ask the browser to make a passkey for the options the API issued. */
export async function createPasskey(
	options: unknown,
): Promise<PasskeyRegistrationJSON> {
	const publicKey = creationOptionsFromJSON(readCreationOptions(options));
	const credential = await ceremony(() =>
		navigator.credentials.create({ publicKey }),
	);
	return registrationToJSON(credential);
}

/** Ask the browser to sign the API's challenge with an existing passkey. */
export async function signInWithPasskey(
	options: unknown,
): Promise<PasskeyAssertionJSON> {
	const publicKey = requestOptionsFromJSON(readRequestOptions(options));
	const credential = await ceremony(() =>
		navigator.credentials.get({ publicKey }),
	);
	return assertionToJSON(credential);
}

/** True when the person dismissed the prompt or it timed out: normal, not a fault. */
export function isCancelled(error: unknown): boolean {
	return (
		error instanceof DOMException &&
		(error.name === "NotAllowedError" || error.name === "AbortError")
	);
}
