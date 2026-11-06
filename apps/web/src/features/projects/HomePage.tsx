/**
 * Home (UI_UX_SPEC §7.1), the P1 part: recent projects, or how to start.
 * Needs-you, pinned projects and activity need endpoints the API does not
 * have yet, so they are left out rather than faked.
 */

import {
	Button,
	Link as ChakraLink,
	Grid,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useApi } from "../../app/api-context";
import { projectListQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { SectionHeading } from "../../ui/SectionHeading";
import {
	fullDate,
	newestFirst,
	type Project,
	roleLabel,
	shortDate,
} from "./format";

const RECENT_COUNT = 8;

function ProjectCard({ project }: { project: Project }) {
	return (
		<ChakraLink
			asChild
			display="flex"
			flexDirection="column"
			gap="3"
			h="full"
			p="5"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
			color="fg"
			textDecoration="none"
			transition="border-color 0.2s ease, background-color 0.2s ease"
			_hover={{
				borderColor: "border.emphasized",
				bg: "bg.subtle",
				textDecoration: "none",
			}}
		>
			<Link to="/p/$projectId" params={{ projectId: project.id }}>
				<Text
					as="span"
					fontFamily="heading"
					fontWeight="semibold"
					fontSize="xl"
					lineHeight="1.25"
				>
					{project.name}
				</Text>
				<Text as="span" fontSize="sm" color="fg.muted">
					{roleLabel(project.role)} · created{" "}
					<time
						dateTime={project.created_at}
						title={fullDate(project.created_at)}
					>
						{shortDate(project.created_at)}
					</time>
				</Text>
			</Link>
		</ChakraLink>
	);
}

function NewProjectButton() {
	return (
		<Button asChild alignSelf="flex-start">
			<Link to="/projects/new">
				<Plus />
				New project
			</Link>
		</Button>
	);
}

function StartHere() {
	return (
		<section aria-labelledby="start-heading">
			<SectionHeading id="start-heading">Start with a question</SectionHeading>
			<Stack gap="6">
				<Grid
					as="ol"
					listStyle="none"
					m="0"
					p="0"
					gap="4"
					templateColumns={{ base: "1fr", md: "repeat(3, minmax(0, 1fr))" }}
				>
					{STEPS.map(([title, detail], index) => (
						<Stack
							as="li"
							key={title}
							gap="2"
							p="5"
							rounded="card"
							borderWidth="1px"
							borderColor="border"
							bg="bg.panel"
						>
							<Text
								as="span"
								fontSize="xs"
								textTransform="uppercase"
								letterSpacing="0.08em"
								color="fg.muted"
							>
								Step {index + 1}
							</Text>
							<Text as="span">
								<strong>{title}</strong> {detail}
							</Text>
						</Stack>
					))}
				</Grid>
				<NewProjectButton />
			</Stack>
		</section>
	);
}

const STEPS: readonly (readonly [string, string])[] = [
	["Ask.", "Say what decision you need evidence for."],
	["Review the search.", "Check the search plan before anything runs."],
	[
		"Read the brief.",
		"Compare the options, with every claim linked to its source.",
	],
];

export function HomePage() {
	const api = useApi();
	const projects = useQuery(projectListQuery(api));

	return (
		<Page>
			<PageHeading title="Home" />
			{projects.isPending ? <Loading what="projects" /> : null}
			{projects.isError ? (
				<ErrorNotice
					error={projects.error}
					onRetry={() => void projects.refetch()}
				/>
			) : null}
			{projects.isSuccess && projects.data.length === 0 ? <StartHere /> : null}
			{projects.isSuccess && projects.data.length > 0 ? (
				<section aria-labelledby="recent-heading">
					<SectionHeading id="recent-heading">Recent projects</SectionHeading>
					<Grid
						as="ul"
						listStyle="none"
						m="0"
						p="0"
						gap="4"
						templateColumns="repeat(auto-fill, minmax(min(100%, 16rem), 1fr))"
					>
						{newestFirst(projects.data)
							.slice(0, RECENT_COUNT)
							.map((project) => (
								<li key={project.id}>
									<ProjectCard project={project} />
								</li>
							))}
					</Grid>
				</section>
			) : null}
		</Page>
	);
}
