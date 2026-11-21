/**
 * The words of the retention period (UI_UX_SPEC §7.7, §14). The API works
 * out every date and number; this only says them.
 */

import { plural } from "../reviews/format";

/** A Needs you row: "Deleted in 14 days unless kept". */
export const deletedInText = (daysLeft: number): string =>
	daysLeft === 0
		? "Deleted today unless kept"
		: `Deleted in ${plural(daysLeft, "day")} unless kept`;

/** The deployment's period, as owners and administrators are told it. */
export const retentionPolicyText = (periodDays: number | null): string =>
	periodDays === null
		? "This deployment never deletes projects for age."
		: `This deployment deletes a project ${plural(periodDays, "day")} after its last activity, unless an owner keeps it.`;
