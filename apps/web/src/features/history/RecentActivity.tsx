/**
 * Home's "Recent activity" (UI_UX_SPEC §7.1): L0 lines from the person's
 * projects, grouped by day. The API picks the lines; this groups and says them.
 */

import { Flex, Heading, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../app/api-context";
import { activityQuery } from "../../app/historyQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { SectionHeading } from "../../ui/SectionHeading";
import { fullDate, shortDate } from "../projects/format";
import { clockTime } from "../reviews/format";
import { ActivityLink } from "./ActivityLink";
import { EventIcon } from "./EventIcon";
import { type ActivityEvent, byDay, eventKey, eventText } from "./words";

/** The time on a line: the clock for today and yesterday, else the date. */
export function LineTime({ at, group }: { at: string; group: string }) {
	const isRecent = group === "Today" || group === "Yesterday";
	return (
		<Text
			as="span"
			flex="none"
			w="14"
			color="fg.muted"
			fontSize="sm"
			fontVariantNumeric="tabular-nums"
			pt="0.5"
		>
			<time dateTime={at} title={fullDate(at)}>
				{isRecent ? clockTime(at) : shortDate(at)}
			</time>
		</Text>
	);
}

function Line({ event, group }: { event: ActivityEvent; group: string }) {
	return (
		<Flex as="li" gap="2" py="1.5" align="flex-start">
			<LineTime at={event.at} group={group} />
			<EventIcon event={event} />
			<Text minW="0">
				<ActivityLink event={event}>{eventText(event)}</ActivityLink>
				<Text as="span" color="fg.muted">
					{" "}
					· {event.project.name}
				</Text>
			</Text>
		</Flex>
	);
}

export function RecentActivity() {
	const api = useApi();
	const activity = useQuery(activityQuery(api));

	if (activity.isSuccess && activity.data.events.length === 0) {
		return null;
	}
	return (
		<section aria-labelledby="activity-heading">
			<SectionHeading id="activity-heading">Recent activity</SectionHeading>
			{activity.isPending ? <Loading what="recent activity" /> : null}
			{activity.isError ? (
				<ErrorNotice
					error={activity.error}
					onRetry={() => void activity.refetch()}
				/>
			) : null}
			{activity.isSuccess ? (
				<Stack gap="5">
					{byDay(activity.data.events).map(({ group, items }) => (
						<Stack key={group} gap="1">
							<Heading as="h3" fontSize="sm" fontWeight="semibold">
								{group}
							</Heading>
							<Stack as="ul" listStyle="none" m="0" p="0" gap="0">
								{items.map((event) => (
									<Line key={eventKey(event)} event={event} group={group} />
								))}
							</Stack>
						</Stack>
					))}
				</Stack>
			) : null}
		</section>
	);
}
