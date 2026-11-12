/** All projects (UI_UX_SPEC §7.2), the P1 part: one list, no grouping or filters yet. */

import {
	Button,
	Link as ChakraLink,
	Checkbox,
	Flex,
	Table,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useApi } from "../../app/api-context";
import { projectListQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { ProjectBadge } from "../../ui/ProjectBadge";
import {
	fullDate,
	newestFirst,
	type Project,
	roleLabel,
	shortDate,
} from "./format";
import { ProjectStatusBadge } from "./ProjectStatusBadge";

function ProjectTable({ projects }: { projects: readonly Project[] }) {
	return (
		<Table.ScrollArea
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
		>
			<Table.Root size="md">
				<Table.Caption srOnly>Your projects</Table.Caption>
				<Table.Header>
					<Table.Row bg="bg.subtle">
						{["Name", "Status", "Your role", "Created"].map((column) => (
							<Table.ColumnHeader
								key={column}
								scope="col"
								fontSize="xs"
								fontWeight="normal"
								textTransform="uppercase"
								letterSpacing="0.08em"
								color="fg.muted"
							>
								{column}
							</Table.ColumnHeader>
						))}
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{newestFirst(projects).map((project) => (
						<Table.Row key={project.id} bg="transparent">
							<Table.Cell as="th" scope="row" fontWeight="normal">
								<ChakraLink asChild css={linkStyle} gap="3">
									<Link to="/p/$projectId" params={{ projectId: project.id }}>
										<ProjectBadge name={project.name} />
										{project.name}
									</Link>
								</ChakraLink>
							</Table.Cell>
							<Table.Cell>
								<ProjectStatusBadge status={project.status} />
							</Table.Cell>
							<Table.Cell>{roleLabel(project.role)}</Table.Cell>
							<Table.Cell>
								<time
									dateTime={project.created_at}
									title={fullDate(project.created_at)}
								>
									{shortDate(project.created_at)}
								</time>
							</Table.Cell>
						</Table.Row>
					))}
				</Table.Body>
			</Table.Root>
		</Table.ScrollArea>
	);
}

export function ProjectsPage() {
	const api = useApi();
	const [includeArchived, setIncludeArchived] = useState(false);
	const projects = useQuery(projectListQuery(api, includeArchived));

	return (
		<Page>
			<Flex wrap="wrap" align="center" justify="space-between" gap="4">
				<PageHeading title="All projects" />
				<Button asChild>
					<Link to="/projects/new">
						<Plus />
						New project
					</Link>
				</Button>
			</Flex>
			<Checkbox.Root
				checked={includeArchived}
				onCheckedChange={(details) =>
					setIncludeArchived(details.checked === true)
				}
			>
				<Checkbox.HiddenInput />
				<Checkbox.Control />
				<Checkbox.Label>Show archived projects</Checkbox.Label>
			</Checkbox.Root>
			{projects.isPending ? <Loading what="projects" /> : null}
			{projects.isError ? (
				<ErrorNotice
					error={projects.error}
					onRetry={() => void projects.refetch()}
				/>
			) : null}
			{projects.isSuccess && projects.data.length === 0 ? (
				<Text color="fg.muted">You have no projects yet.</Text>
			) : null}
			{projects.isSuccess && projects.data.length > 0 ? (
				<ProjectTable projects={projects.data} />
			) : null}
		</Page>
	);
}
