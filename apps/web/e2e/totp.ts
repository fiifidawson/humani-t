/** A time-based one-time code (RFC 6238: SHA-1, 30 s steps, 6 digits), as an authenticator app makes it. */

import { createHmac } from "node:crypto";

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function fromBase32(text: string): Buffer {
	let bits = "";
	for (const character of text.replace(/=+$/, "").toUpperCase()) {
		const value = BASE32.indexOf(character);
		if (value < 0) {
			throw new Error(`Not base32: ${character}`);
		}
		bits += value.toString(2).padStart(5, "0");
	}
	const bytes = bits.match(/.{8}/g) ?? [];
	return Buffer.from(bytes.map((byte) => Number.parseInt(byte, 2)));
}

/** The code for the step `stepsAhead` after now; a code is accepted only once, so a second sign-in uses the next. */
export function totpCode(secret: string, stepsAhead = 0): string {
	const step = Math.floor(Date.now() / 30_000) + stepsAhead;
	const counter = Buffer.alloc(8);
	counter.writeBigUInt64BE(BigInt(step));
	const digest = createHmac("sha1", fromBase32(secret))
		.update(counter)
		.digest();
	const offset = (digest.at(-1) ?? 0) & 0x0f;
	const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
	return value.toString().padStart(6, "0");
}

/** The secret in an `otpauth://totp/…?secret=…` link. */
export function secretOf(otpauthUri: string): string {
	const secret = new URL(otpauthUri).searchParams.get("secret");
	if (secret === null) {
		throw new Error("The otpauth link has no secret");
	}
	return secret;
}
