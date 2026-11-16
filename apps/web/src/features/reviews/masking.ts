/**
 * Masked text as the API returns it: placeholders such as `[LOCATION_1]` in
 * place of what was masked (UI_UX_SPEC §8.5). This reads them out; it never
 * sees, and cannot show, the original values.
 */

import type { components } from "../../api/schema.gen";

export type EntityType = components["schemas"]["EntityType"];

export interface Placeholder {
	type: EntityType;
	number: number;
}

/** Singular and plural, for "1 location, 2 people". */
const WORDS: Readonly<Record<EntityType, [string, string]>> = {
	PERSON: ["person", "people"],
	LOCATION: ["location", "locations"],
	DATE: ["date", "dates"],
	EMAIL: ["email address", "email addresses"],
	PHONE: ["phone number", "phone numbers"],
	MEDICAL_RECORD: ["medical record number", "medical record numbers"],
	ID_NUMBER: ["ID number", "ID numbers"],
	PASSPORT: ["passport number", "passport numbers"],
	IBAN: ["bank account number", "bank account numbers"],
	CARD_NUMBER: ["card number", "card numbers"],
	IP_ADDRESS: ["IP address", "IP addresses"],
	COORDINATES: ["set of coordinates", "sets of coordinates"],
};

const isEntityType = (value: string): value is EntityType =>
	Object.hasOwn(WORDS, value);

/** Every type, in the order a menu offers them. */
export const ENTITY_TYPES: readonly EntityType[] =
	Object.keys(WORDS).filter(isEntityType);

/** "Person", "Email address": a type's name on its own, as a menu shows it. */
export function entityTypeName(type: EntityType): string {
	const [one] = WORDS[type];
	const name = type === "COORDINATES" ? "coordinates" : one;
	return `${name.charAt(0).toUpperCase()}${name.slice(1)}`;
}

const PLACEHOLDER = /\[([A-Z]+(?:_[A-Z]+)*)_(\d+)\]/g;

/** The text in order: plain strings, and a placeholder wherever one stood. */
export function splitMasked(text: string): (string | Placeholder)[] {
	const parts: (string | Placeholder)[] = [];
	let plain = "";
	let last = 0;
	for (const match of text.matchAll(PLACEHOLDER)) {
		const [whole, type = "", number = "0"] = match;
		plain += text.slice(last, match.index);
		last = match.index + whole.length;
		if (isEntityType(type)) {
			if (plain !== "") {
				parts.push(plain);
			}
			plain = "";
			parts.push({ type, number: Number(number) });
		} else {
			plain += whole;
		}
	}
	plain += text.slice(last);
	if (plain !== "") {
		parts.push(plain);
	}
	return parts;
}

/** "masked location 1": what a screen reader says for a pill. */
export function spokenPlaceholder({ type, number }: Placeholder): string {
	return `masked ${type.toLowerCase().replaceAll("_", " ")} ${number}`;
}

/** "Location 1", "Medical record 2": what the pill shows. */
export function shownPlaceholder({ type, number }: Placeholder): string {
	const words = type.toLowerCase().replaceAll("_", " ");
	return `${words.charAt(0).toUpperCase()}${words.slice(1)} ${number}`;
}

const LIST = new Intl.ListFormat("en-GB", { type: "conjunction" });

/** Each type's count in words: ["1 location", "2 people"]. */
export function maskedCountList(
	counts: Readonly<Record<string, number>>,
): string[] {
	return Object.entries(counts)
		.filter(([, count]) => count > 0)
		.map(([type, count]) => {
			const [one, many] = isEntityType(type)
				? WORDS[type]
				: [type.toLowerCase(), type.toLowerCase()];
			return `${count} ${count === 1 ? one : many}`;
		});
}

/** Counts by type in words: "1 location, 2 people and 1 phone number". */
export function maskedCounts(counts: Readonly<Record<string, number>>): string {
	return LIST.format(maskedCountList(counts));
}

export const totalMasked = (counts: Readonly<Record<string, number>>): number =>
	Object.values(counts).reduce((sum, count) => sum + count, 0);
