import { afterEach, describe, expect, it } from "vitest";
import {
	installFakeAuthenticator,
	uninstallFakeAuthenticator,
} from "../../test/fakeAuthenticator";
import {
	base64urlToBuffer,
	bufferToBase64url,
	createPasskey,
	isCancelled,
	isPasskeySupported,
	signInWithPasskey,
} from "./webauthn";

const text = (source: BufferSource | undefined) =>
	source === undefined ? "" : new TextDecoder().decode(source);

afterEach(() => {
	uninstallFakeAuthenticator();
});

describe("base64url, the encoding WebAuthn JSON uses for bytes", () => {
	it("round-trips bytes, including the ones base64 would write as + and /", () => {
		const bytes = new Uint8Array([0xfb, 0xff, 0xbf, 0x00, 0x10]);

		const encoded = bufferToBase64url(bytes.buffer);

		expect(encoded).toBe("-_-_ABA");
		expect(new Uint8Array(base64urlToBuffer(encoded))).toEqual(bytes);
	});

	it("never pads with =", () => {
		expect(bufferToBase64url(new TextEncoder().encode("a").buffer)).toBe("YQ");
	});
});

describe("registering a passkey", () => {
	const options: PublicKeyCredentialCreationOptionsJSON = {
		challenge: "Y2hhbGxlbmdl",
		rp: { id: "localhost", name: "humani-t" },
		user: { id: "dXNlcg", name: "amara@example.org", displayName: "Amara N." },
		pubKeyCredParams: [{ type: "public-key", alg: -7 }],
		excludeCredentials: [{ id: "Y3JlZC0x", type: "public-key" }],
		authenticatorSelection: { userVerification: "required" },
		attestation: "none",
	};

	it("hands the browser bytes where the API sent base64url", async () => {
		const authenticator = installFakeAuthenticator();

		await createPasskey(options);

		const publicKey = authenticator.creations[0]?.publicKey;
		expect(text(publicKey?.challenge)).toBe("challenge");
		expect(text(publicKey?.user.id)).toBe("user");
		expect(text(publicKey?.excludeCredentials?.[0]?.id)).toBe("cred-1");
		expect(publicKey?.authenticatorSelection?.userVerification).toBe(
			"required",
		);
	});

	it("returns the credential as RegistrationResponseJSON for the API", async () => {
		installFakeAuthenticator();

		const credential = await createPasskey(options);

		expect(credential).toEqual({
			id: "Y3JlZC0x",
			rawId: bufferToBase64url(new TextEncoder().encode("cred-1").buffer),
			type: "public-key",
			authenticatorAttachment: "platform",
			clientExtensionResults: {},
			response: {
				clientDataJSON: bufferToBase64url(
					new TextEncoder().encode('{"type":"webauthn.create"}').buffer,
				),
				attestationObject: bufferToBase64url(
					new TextEncoder().encode("attestation").buffer,
				),
				transports: ["internal"],
			},
		});
	});
});

describe("signing in with a passkey", () => {
	it("returns the assertion as AuthenticationResponseJSON for the API", async () => {
		const authenticator = installFakeAuthenticator();

		const assertion = await signInWithPasskey({
			challenge: "Y2hhbGxlbmdl",
			rpId: "localhost",
			allowCredentials: [{ id: "Y3JlZC0x", type: "public-key" }],
			userVerification: "required",
		});

		expect(text(authenticator.assertions[0]?.publicKey?.challenge)).toBe(
			"challenge",
		);
		expect(assertion.response.signature).toBe(
			bufferToBase64url(new TextEncoder().encode("signature").buffer),
		);
		expect(assertion.response.userHandle).toBe(
			bufferToBase64url(new TextEncoder().encode("user-handle-01").buffer),
		);
	});

	it("refuses options missing the challenge, before the browser is asked", async () => {
		const authenticator = installFakeAuthenticator();

		await expect(signInWithPasskey({ rpId: "localhost" })).rejects.toThrow(
			"The passkey options from the server are incomplete.",
		);
		expect(authenticator.assertions).toHaveLength(0);
	});

	it("knows a cancelled ceremony from a broken one", async () => {
		const authenticator = installFakeAuthenticator();
		authenticator.cancelNext();

		const error = await signInWithPasskey({ challenge: "Y2hhbGxlbmdl" }).catch(
			(e: unknown) => e,
		);

		expect(isCancelled(error)).toBe(true);
		expect(isCancelled(new Error("boom"))).toBe(false);
	});
});

describe("support", () => {
	it("is reported only where the browser has WebAuthn", () => {
		expect(isPasskeySupported()).toBe(false);

		installFakeAuthenticator();

		expect(isPasskeySupported()).toBe(true);
	});
});
