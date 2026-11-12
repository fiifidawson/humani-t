/**
 * A project's status as a small badge (UI_UX_SPEC §11.1). The API derives the
 * status; this file only words it.
 */

import { Badge } from "@chakra-ui/react";
import type { Project } from "./format";

type ProjectStatus = Project["status"];

const STATUS_LABELS: Readonly<Record<ProjectStatus, string>> = {
	planning: "Planning",
	running: "Running",
	needs_you: "Needs you",
	brief_ready: "Brief ready",
	done_without_brief: "Done, no brief",
	failed: "Failed",
	cancelled: "Cancelled",
	archived: "Archived",
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
	return (
		<Badge
			variant="outline"
			colorPalette={status === "failed" ? "danger" : "gray"}
			alignSelf="flex-start"
		>
			{STATUS_LABELS[status]}
		</Badge>
	);
}
