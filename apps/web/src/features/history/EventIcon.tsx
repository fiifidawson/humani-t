/**
 * The small icon in front of an activity line or a Needs you row. It repeats
 * what the words say, so it is hidden from screen readers.
 */

import { Box } from "@chakra-ui/react";
import {
	Check,
	CircleHelp,
	Diamond,
	FileText,
	type LucideIcon,
	Pause,
	PencilLine,
	Play,
	SlidersHorizontal,
	X,
} from "lucide-react";
import type { ActivityEvent, NeedsYouItem } from "./words";

function iconOf(event: ActivityEvent): LucideIcon {
	switch (event.action) {
		case "run":
			if (event.run_state === "done") return Check;
			if (event.run_state === "waiting") return Pause;
			if (event.run_state === "failed" || event.run_state === "cancelled")
				return X;
			return Play;
		case "brief_ready":
			return Diamond;
		case "question_asked":
		case "review_started":
			return CircleHelp;
		case "document_added":
			return FileText;
		case "labels_changed":
			return SlidersHorizontal;
		default:
			return PencilLine;
	}
}

const NEEDS_ICONS: Readonly<Record<NeedsYouItem["kind"], LucideIcon>> = {
	gate: Pause,
	draft: PencilLine,
	failed_run: X,
	unread_brief: Diamond,
};

function Icon({ icon: Shown }: { icon: LucideIcon }) {
	return (
		<Box as="span" aria-hidden="true" flex="none" color="fg.muted" pt="0.5">
			<Shown size={16} />
		</Box>
	);
}

export const EventIcon = ({ event }: { event: ActivityEvent }) => (
	<Icon icon={iconOf(event)} />
);

export const NeedsYouIcon = ({ item }: { item: NeedsYouItem }) => (
	<Icon icon={NEEDS_ICONS[item.kind]} />
);
