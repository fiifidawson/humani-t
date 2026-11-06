/**
 * A software stand-in for `navigator.credentials`, which jsdom does not have.
 *
 * It answers `create()` and `get()` with objects shaped like the browser's
 * PublicKeyCredential (ArrayBuffers, not strings), so the code that turns them
 * into JSON for the API is exercised exactly as in a browser. The fake API
 * checks that the JSON it receives has every field the server verifies.
 */

const bytes = (text: string): ArrayBuffer =>
	new TextEncoder().encode(text).buffer;

export interface FakeAuthenticator {
	readonly creations: CredentialCreationOptions[];
	readonly assertions: CredentialRequestOptions[];
	/** Make the next ceremony fail as a browser does when the person cancels. */
	cancelNext(): void;
}

export function installFakeAuthenticator(): FakeAuthenticator {
	let shouldCancel = false;
	const creations: CredentialCreationOptions[] = [];
	const assertions: CredentialRequestOptions[] = [];

	const refuseIfCancelled = () => {
		if (shouldCancel) {
			shouldCancel = false;
			throw new DOMException(
				"The operation either timed out or was not allowed.",
				"NotAllowedError",
			);
		}
	};

	const credentials = {
		create: async (options: CredentialCreationOptions) => {
			creations.push(options);
			refuseIfCancelled();
			return {
				id: "Y3JlZC0x",
				rawId: bytes("cred-1"),
				type: "public-key",
				authenticatorAttachment: "platform",
				response: {
					clientDataJSON: bytes('{"type":"webauthn.create"}'),
					attestationObject: bytes("attestation"),
					getTransports: () => ["internal"],
				},
				getClientExtensionResults: () => ({}),
			};
		},
		get: async (options: CredentialRequestOptions) => {
			assertions.push(options);
			refuseIfCancelled();
			return {
				id: "Y3JlZC0x",
				rawId: bytes("cred-1"),
				type: "public-key",
				authenticatorAttachment: "platform",
				response: {
					clientDataJSON: bytes('{"type":"webauthn.get"}'),
					authenticatorData: bytes("authenticator-data"),
					signature: bytes("signature"),
					userHandle: bytes("user-handle-01"),
				},
				getClientExtensionResults: () => ({}),
			};
		},
	};

	Object.defineProperty(navigator, "credentials", {
		value: credentials,
		configurable: true,
	});
	Object.defineProperty(window, "PublicKeyCredential", {
		value: class {},
		configurable: true,
	});

	return {
		creations,
		assertions,
		cancelNext: () => {
			shouldCancel = true;
		},
	};
}

export function uninstallFakeAuthenticator(): void {
	Reflect.deleteProperty(navigator, "credentials");
	Reflect.deleteProperty(window, "PublicKeyCredential");
}
