import { describe, expect, it } from "vitest";
import { scoreWord, screeningWord, stoppedBecause } from "./words";

describe("why the loop stopped, in words (UI_UX_SPEC §6.9 header, §6.11)", () => {
	it.each([
		["accepted", "accepted by the Critic"],
		["max_rounds", "reached the round limit (3)"],
		["invalid_output", "a step gave unusable answers twice in a row"],
		["no_progress", "no new evidence and no better score"],
	] as const)("says %s as “%s”", (reason, words) => {
		expect(stoppedBecause(reason, 3)).toBe(words);
	});
});

describe("labels and scores", () => {
	it("names the screening label as people read it", () => {
		expect(screeningWord("include")).toBe("Included");
		expect(screeningWord(null)).toBeNull();
	});

	it("puts each Critic score in words, never as a total", () => {
		expect([0, 1, 2].map(scoreWord)).toEqual(["poor", "partly", "good"]);
	});
});
