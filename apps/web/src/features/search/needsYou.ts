/**
 * "Needs you" in the palette's empty box (UI_UX_SPEC §7.6): the same list as
 * Home's (§7.1), one line each, so the palette never decides what is listed.
 */

import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../app/api-context";
import { needsYouQuery } from "../../app/historyQueries";
import { needsYouText } from "../history/words";

export interface NeedsYouItem {
	id: string;
	projectId: string;
	/** One line, as Home shows it: "Run 3 stopped at Screen · Cash vs vouchers, Gao". */
	label: string;
}

export function useNeedsYouItems(): readonly NeedsYouItem[] {
	const api = useApi();
	const needsYou = useQuery(needsYouQuery(api));
	return (needsYou.data?.items ?? []).map((item) => ({
		id: item.key,
		projectId: item.project.id,
		label: `${needsYouText(item)} · ${item.project.name}`,
	}));
}
