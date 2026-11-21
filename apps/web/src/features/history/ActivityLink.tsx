/**
 * The one action of an activity line or a Needs you row: a link to the
 * place it is about (UI_UX_SPEC §7.1, §7.4). Ids come from the API.
 */

import { Link as ChakraLink } from "@chakra-ui/react";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { ActivityEvent, NeedsYouItem } from "./words";

const lineLink = {
	color: "fg",
	textDecoration: "none",
	textUnderlineOffset: "3px",
	_hover: { textDecoration: "underline" },
} as const;

function Target({
	event,
	children,
}: {
	event: ActivityEvent;
	children: ReactNode;
}) {
	const projectId = event.project.id;
	const reviewId = event.review_id ?? "";
	switch (event.action) {
		case "plan_drafted":
		case "plan_accepted":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/draft"
					params={{ projectId, reviewId }}
					search={{ version: event.version ?? undefined }}
				>
					{children}
				</Link>
			);
		case "labels_changed":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/papers"
					params={{ projectId, reviewId }}
					search={{}}
				>
					{children}
				</Link>
			);
		case "run":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/run/$runId"
					params={{ projectId, reviewId, runId: event.run_id ?? "" }}
				>
					{children}
				</Link>
			);
		case "brief_ready":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/brief"
					params={{ projectId, reviewId }}
					search={{ run: event.run_id ?? undefined }}
				>
					{children}
				</Link>
			);
		case "question_asked":
			return (
				<Link
					to="/p/$projectId/ask/$threadId"
					params={{ projectId, threadId: event.thread_id ?? "" }}
				>
					{children}
				</Link>
			);
		case "document_added":
			return (
				<Link
					to="/p/$projectId/documents"
					params={{ projectId }}
					search={{ document: event.document_id ?? undefined }}
				>
					{children}
				</Link>
			);
		case "review_started":
			return (
				<Link to="/p/$projectId/r/$reviewId" params={{ projectId, reviewId }}>
					{children}
				</Link>
			);
	}
}

export function ActivityLink({
	event,
	children,
}: {
	event: ActivityEvent;
	children: ReactNode;
}) {
	return (
		<ChakraLink asChild css={lineLink} overflowWrap="anywhere">
			<Target event={event}>{children}</Target>
		</ChakraLink>
	);
}

/** Where a Needs you row's action goes. */
export function NeedsYouTarget({
	item,
	label,
	children,
}: {
	item: NeedsYouItem;
	label: string;
	children: ReactNode;
}) {
	const projectId = item.project.id;
	const reviewId = item.review_id ?? "";
	switch (item.kind) {
		case "draft":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/draft"
					params={{ projectId, reviewId }}
					search={{ version: item.version ?? undefined }}
					aria-label={label}
				>
					{children}
				</Link>
			);
		case "unread_brief":
			return (
				<Link
					to="/p/$projectId/r/$reviewId/brief"
					params={{ projectId, reviewId }}
					search={{ run: item.run_id ?? undefined }}
					aria-label={label}
				>
					{children}
				</Link>
			);
		default:
			return (
				<Link
					to="/p/$projectId/r/$reviewId/run/$runId"
					params={{ projectId, reviewId, runId: item.run_id ?? "" }}
					aria-label={label}
				>
					{children}
				</Link>
			);
	}
}
