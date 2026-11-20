/**
 * A project's timeline (UI_UX_SPEC §7.4): every line grouped by review,
 * newest first, each with one action; runs open to their stages. The "Show:"
 * filters are a view preference, kept in this browser for each person.
 */

import {
	Box,
	Button,
	Flex,
	Heading,
	IconButton,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { timelineQuery } from "../../app/historyQueries";
import { sessionQuery } from "../../app/queries";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Reveal } from "../../ui/Reveal";
import { shortDate } from "../projects/format";
import { duration } from "../reviews/format";
import { isStageId } from "../runs/runEvents";
import { stageName } from "../runs/stages";
import { ActivityLink } from "./ActivityLink";
import { EventIcon } from "./EventIcon";
import { LineTime } from "./RecentActivity";
import {
	ACTIVITY_KINDS,
	type ActivityEvent,
	type ActivityKind,
	dayGroup,
	eventKey,
	eventText,
} from "./words";

type Group = components["schemas"]["HistoryGroupOut"];
type StageLine = components["schemas"]["StageLineOut"];

const STAGE_STATES: Readonly<Record<StageLine["state"], string>> = {
	done: "done",
	running: "running",
	waiting: "waiting for a decision",
	stopped: "stopped here",
	not_run: "not run",
};

const storageKey = (userId: string) => `humani-t:history-hidden:${userId}`;

/** The kinds this person hid, read from this browser; none if storage refuses. */
function readHidden(userId: string): ActivityKind[] {
	try {
		const stored: unknown = JSON.parse(
			window.localStorage.getItem(storageKey(userId)) ?? "[]",
		);
		return ACTIVITY_KINDS.map((k) => k.kind).filter(
			(kind) => Array.isArray(stored) && stored.includes(kind),
		);
	} catch {
		return [];
	}
}

function useHiddenKinds(userId: string) {
	const [hidden, setHidden] = useState(() => readHidden(userId));
	const toggle = (kind: ActivityKind) => {
		const next = hidden.includes(kind)
			? hidden.filter((k) => k !== kind)
			: [...hidden, kind];
		setHidden(next);
		try {
			window.localStorage.setItem(storageKey(userId), JSON.stringify(next));
		} catch {
			// Kept for this page view only.
		}
	};
	return [hidden, toggle] as const;
}

function Stages({ stages, id }: { stages: StageLine[]; id: string }) {
	return (
		<Reveal>
			<Stack as="ol" id={id} listStyle="none" m="0" ps="20" py="1" gap="0.5">
				{stages.map((line) => (
					<Text as="li" key={line.stage} fontSize="sm" color="fg.muted">
						{isStageId(line.stage) ? stageName(line.stage) : line.stage} ·{" "}
						{STAGE_STATES[line.state]}
						{line.seconds === null ? "" : ` · ${duration(line.seconds)}`}
					</Text>
				))}
			</Stack>
		</Reveal>
	);
}

function Line({ event }: { event: ActivityEvent }) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const text = eventText(event);
	return (
		<Box as="li">
			<Flex gap="2" py="1.5" align="flex-start">
				<LineTime at={event.at} group={dayGroup(event.at)} />
				<EventIcon event={event} />
				<Text minW="0" flex="1">
					<ActivityLink event={event}>{text}</ActivityLink>
				</Text>
				{event.stages.length === 0 ? null : (
					<IconButton
						variant="ghost"
						size="2xs"
						aria-label={`Stages of ${text}`}
						aria-expanded={isOpen}
						aria-controls={id}
						onClick={() => setOpen(!isOpen)}
						css={{
							"& svg": {
								transition: "transform 0.15s ease",
								transform: isOpen ? "rotate(90deg)" : "none",
							},
						}}
					>
						<ChevronRight />
					</IconButton>
				)}
			</Flex>
			{isOpen ? <Stages stages={event.stages} id={id} /> : null}
		</Box>
	);
}

function ReviewGroup({
	group,
	hidden,
	isFirst,
}: {
	group: Group;
	hidden: readonly ActivityKind[];
	isFirst: boolean;
}) {
	const [isOpen, setOpen] = useState(isFirst);
	const id = useId();
	const shown = group.events.filter((event) => !hidden.includes(event.kind));
	const title =
		group.review === null ? "Project" : `Review: ${group.review.question}`;
	return (
		<Box as="section" aria-labelledby={`${id}-heading`}>
			<Flex gap="2" align="center">
				<Heading
					as="h3"
					id={`${id}-heading`}
					fontSize="md"
					fontWeight="semibold"
					flex="1"
					minW="0"
				>
					<DisclosureButton
						isOpen={isOpen}
						controls={id}
						onToggle={() => setOpen(!isOpen)}
					>
						<Text as="span" truncate>
							{title}
						</Text>
					</DisclosureButton>
				</Heading>
				{group.review === null ? null : (
					<Text color="fg.muted" fontSize="sm" flex="none">
						started {shortDate(group.review.started_at)}
					</Text>
				)}
			</Flex>
			{isOpen ? (
				<Reveal>
					<Box id={id} ps="2" borderStartWidth="1px" borderColor="border">
						{shown.length === 0 ? (
							<Text color="fg.muted" py="2" ps="2">
								Nothing to show with these filters.
							</Text>
						) : (
							<Stack as="ul" listStyle="none" m="0" p="0" gap="0">
								{shown.map((event) => (
									<Line key={eventKey(event)} event={event} />
								))}
							</Stack>
						)}
					</Box>
				</Reveal>
			) : null}
		</Box>
	);
}

function Timeline({ userId, groups }: { userId: string; groups: Group[] }) {
	const [hidden, toggle] = useHiddenKinds(userId);
	return (
		<Stack gap="6">
			<Flex role="group" aria-label="Show" gap="2" wrap="wrap" align="center">
				<Text as="span" color="fg.muted" fontSize="sm">
					Show:
				</Text>
				{ACTIVITY_KINDS.map(({ kind, name }) => {
					const isShown = !hidden.includes(kind);
					return (
						<Button
							key={kind}
							size="xs"
							variant={isShown ? "solid" : "outline"}
							aria-pressed={isShown}
							onClick={() => toggle(kind)}
						>
							{name}
						</Button>
					);
				})}
			</Flex>
			{groups.length === 0 ? (
				<Text>Nothing has happened in this project yet.</Text>
			) : (
				groups.map((group, index) => (
					<ReviewGroup
						key={group.review?.id ?? "project"}
						group={group}
						hidden={hidden}
						isFirst={index === 0}
					/>
				))
			)}
			{/* Compare runs (§7.4): the compare lane's picker goes here, below the timeline. */}
		</Stack>
	);
}

export function HistoryPage() {
	const api = useApi();
	const { projectId } = useParams({ from: "/signed-in/p/$projectId/history" });
	const timeline = useQuery(timelineQuery(api, projectId));
	const session = useQuery(sessionQuery(api));

	return (
		<Box flex="1" minH="0" overflowY="auto">
			<Stack gap="6" maxW="4xl" mx="auto" px={{ base: "4", md: "6" }} py="6">
				<Heading as="h2" fontSize="xl" fontWeight="semibold">
					History
				</Heading>
				{timeline.isError ? (
					<ErrorNotice
						error={timeline.error}
						onRetry={() => void timeline.refetch()}
					/>
				) : timeline.data === undefined || session.data === undefined ? (
					<Loading what="the project's history" />
				) : (
					<Timeline
						userId={session.data.user.id}
						groups={timeline.data.groups}
					/>
				)}
			</Stack>
		</Box>
	);
}
