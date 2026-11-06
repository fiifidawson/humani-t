import { describe, expect, it } from "vitest";
import { maskedCounts, splitMasked } from "./masking";

describe("reading masked text (UI_UX_SPEC §8.5)", () => {
	it("splits placeholders out of the text, keeping type and number", () => {
		expect(splitMasked("Met [PERSON_1] in [LOCATION_2].")).toEqual([
			"Met ",
			{ type: "PERSON", number: 1 },
			" in ",
			{ type: "LOCATION", number: 2 },
			".",
		]);
	});

	it("reads types whose names have an underscore", () => {
		expect(splitMasked("MRN [MEDICAL_RECORD_3]")).toEqual([
			"MRN ",
			{ type: "MEDICAL_RECORD", number: 3 },
		]);
	});

	it("leaves brackets that are not placeholders as text", () => {
		expect(splitMasked("See [1] and [NOT_A_TYPE_1]")).toEqual([
			"See [1] and [NOT_A_TYPE_1]",
		]);
	});

	it("says what was masked as counts in words, never values", () => {
		expect(maskedCounts({ LOCATION: 1, PERSON: 2, PHONE: 1 })).toBe(
			"1 location, 2 people and 1 phone number",
		);
	});
});
