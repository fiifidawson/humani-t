import { describe, expect, it } from "vitest";
import { deletedInText, retentionPolicyText } from "./words";

describe("retention words (UI_UX_SPEC §7.7, §14)", () => {
	it("says the real number of days, one day, and today", () => {
		expect(deletedInText(14)).toBe("Deleted in 14 days unless kept");
		expect(deletedInText(1)).toBe("Deleted in 1 day unless kept");
		expect(deletedInText(0)).toBe("Deleted today unless kept");
	});

	it("says the deployment's period, or that nothing is deleted for age", () => {
		expect(retentionPolicyText(365)).toBe(
			"This deployment deletes a project 365 days after its last activity, unless an owner keeps it.",
		);
		expect(retentionPolicyText(null)).toBe(
			"This deployment never deletes projects for age.",
		);
	});
});
