/**
 * A project as a card (UI_UX_SPEC §7.1 Pinned, §7.2 cards view): where it is
 * filed, its status, its papers and its people, with a pin control.
 *
 * The name is the link and stretches over the card; the pin button sits above
 * it, so the card holds no control inside a link.
 */

import { Box, Link as ChakraLink, Flex, Stack, Text } from "@chakra-ui/react";
import { Link } from "@tanstack/react-router";
import { PinButton } from "../collections/PinButton";
import { plural, relativeTime } from "../reviews/format";
import { fullDate, type ProjectSummary } from "./format";
import { ProjectStatusBadge } from "./ProjectStatusBadge";

interface ProjectSummaryCardProps {
	project: ProjectSummary;
	collectionName: string | undefined;
	/** The other groups it also shows under, when grouped by tag or owner. */
	alsoIn?: readonly string[];
}

export function ProjectSummaryCard({
	project,
	collectionName,
	alsoIn = [],
}: ProjectSummaryCardProps) {
	const where = [collectionName, ...project.tags].filter(
		(part): part is string => part !== undefined,
	);
	return (
		<Stack
			position="relative"
			gap="2"
			h="full"
			p="5"
			pr="12"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
			transition="border-color 0.2s ease, background-color 0.2s ease"
			_hover={{ borderColor: "border.emphasized", bg: "bg.subtle" }}
		>
			<ChakraLink
				asChild
				color="fg"
				fontFamily="heading"
				fontWeight="semibold"
				fontSize="xl"
				lineHeight="1.25"
				textDecoration="none"
				_hover={{ textDecoration: "underline" }}
				_before={{ content: '""', position: "absolute", inset: "0" }}
			>
				<Link to="/p/$projectId" params={{ projectId: project.id }}>
					{project.name}
				</Link>
			</ChakraLink>
			{where.length > 0 ? (
				<Text fontSize="sm" color="fg.muted" truncate>
					{where.join(" · ")}
				</Text>
			) : null}
			<Flex gap="2" align="center" wrap="wrap" fontSize="sm">
				<ProjectStatusBadge status={project.status} />
				{project.brief_version === null ? null : (
					<Text as="span">Brief v{project.brief_version}</Text>
				)}
				<Text
					as="span"
					color="fg.muted"
					title={fullDate(project.last_activity_at)}
				>
					{relativeTime(project.last_activity_at)}
				</Text>
			</Flex>
			{project.relevant_papers === null ||
			project.papers_found === null ? null : (
				<Text fontSize="sm">
					{project.relevant_papers} relevant of {project.papers_found}
				</Text>
			)}
			<Text fontSize="sm" color="fg.muted">
				{plural(project.members.length, "member")}
			</Text>
			{alsoIn.length > 0 ? (
				<Text fontSize="xs" color="fg.muted">
					Also in {alsoIn.join(", ")}
				</Text>
			) : null}
			<Box position="absolute" top="3" right="3">
				<PinButton
					projectId={project.id}
					name={project.name}
					isPinned={project.is_pinned}
				/>
			</Box>
		</Stack>
	);
}
