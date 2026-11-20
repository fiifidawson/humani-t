import { describe, expect, it } from "vitest";
import { byDay, dayGroup } from "./words";

const NOW = new Date(2026, 10, 19, 15, 0);
const at = (year: number, month: number, day: number, hour = 9) =>
	new Date(year, month, day, hour).toISOString();

describe("day groups (UI_UX_SPEC §7.1)", () => {
	it("says Today, Yesterday and This week, then month names", () => {
		expect(dayGroup(at(2026, 10, 19, 1), NOW)).toBe("Today");
		expect(dayGroup(at(2026, 10, 18, 23), NOW)).toBe("Yesterday");
		expect(dayGroup(at(2026, 10, 14), NOW)).toBe("This week");
		expect(dayGroup(at(2026, 9, 2), NOW)).toBe("October");
		expect(dayGroup(at(2025, 10, 30), NOW)).toBe("November 2025");
	});

	it("keeps lines in order under their day", () => {
		const lines = [
			{ at: at(2026, 10, 19), n: 1 },
			{ at: at(2026, 10, 19, 8), n: 2 },
			{ at: at(2026, 10, 18), n: 3 },
		];

		expect(byDay(lines, NOW)).toEqual([
			{ group: "Today", items: [lines[0], lines[1]] },
			{ group: "Yesterday", items: [lines[2]] },
		]);
	});
});
