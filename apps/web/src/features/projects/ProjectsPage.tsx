/**
 * All projects (UI_UX_SPEC §7.2): cards or a dense list, grouped, sorted and
 * filtered, with bulk actions in the list. The page's state lives in its URL.
 *
 * The API filters and sorts, over the person's own projects only; grouping is
 * how this page lays them out.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Grid,
	IconButton,
	Stack,
	Table,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { LayoutGrid, List, Plus } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { useApi } from "../../app/api-context";
import {
	collectionsQuery,
	filteredProjectsQuery,
	projectListQuery,
} from "../../app/queries";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { ProjectBadge } from "../../ui/ProjectBadge";
import { Reveal } from "../../ui/Reveal";
import { plural, relativeTime } from "../reviews/format";
import { BulkActions } from "./BulkActions";
import { fullDate } from "./format";
import { LabelledSelect, ProjectFilters } from "./ProjectFilters";
import { ProjectStatusBadge } from "./ProjectStatusBadge";
import { ProjectSummaryCard } from "./ProjectSummaryCard";
import {
	filtersFor,
	GROUPS,
	type GroupBy,
	groupProjects,
	type ListView,
	type Placed,
	type ProjectGroup,
	type ProjectsSearch,
	SORTS,
} from "./projectListView";

const COLUMNS = [
	"Name",
	"Collection",
	"Tags",
	"Status",
	"Last activity",
	"Members",
	"Relevant papers",
	"Brief",
] as const;

function Cards({
	placed,
	names,
}: {
	placed: readonly Placed[];
	names: ReadonlyMap<string, string>;
}) {
	return (
		<Grid
			as="ul"
			listStyle="none"
			m="0"
			p="0"
			gap="4"
			templateColumns="repeat(auto-fill, minmax(min(100%, 17rem), 1fr))"
		>
			{placed.map(({ project, alsoIn }) => (
				<li key={project.id}>
					<ProjectSummaryCard
						project={project}
						alsoIn={alsoIn}
						collectionName={
							project.collection_id === null
								? undefined
								: names.get(project.collection_id)
						}
					/>
				</li>
			))}
		</Grid>
	);
}

function Rows({
	caption,
	placed,
	names,
	selected,
	onSelect,
}: {
	caption: string;
	placed: readonly Placed[];
	names: ReadonlyMap<string, string>;
	selected: ReadonlySet<string>;
	onSelect: (projectId: string, isSelected: boolean) => void;
}) {
	return (
		<Table.ScrollArea
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
		>
			<Table.Root size="sm">
				<Table.Caption srOnly>{caption}</Table.Caption>
				<Table.Header>
					<Table.Row bg="bg.subtle">
						<Table.ColumnHeader w="10">
							<chakra.span srOnly>Select</chakra.span>
						</Table.ColumnHeader>
						{COLUMNS.map((column) => (
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
					{placed.map(({ project, alsoIn }) => (
						<Table.Row key={project.id} bg="transparent">
							<Table.Cell>
								{project.role === "owner" ? (
									<chakra.input
										type="checkbox"
										aria-label={`Select ${project.name}`}
										checked={selected.has(project.id)}
										onChange={(event) =>
											onSelect(project.id, event.target.checked)
										}
										boxSize="4"
										accentColor="brand.solid"
									/>
								) : null}
							</Table.Cell>
							<Table.Cell as="th" scope="row" fontWeight="normal">
								<ChakraLink asChild css={linkStyle} gap="3">
									<Link to="/p/$projectId" params={{ projectId: project.id }}>
										<ProjectBadge name={project.name} />
										{project.name}
									</Link>
								</ChakraLink>
								{alsoIn.length > 0 ? (
									<Text fontSize="xs" color="fg.muted">
										Also in {alsoIn.join(", ")}
									</Text>
								) : null}
							</Table.Cell>
							<Table.Cell>
								{project.collection_id === null
									? "—"
									: (names.get(project.collection_id) ?? "—")}
							</Table.Cell>
							<Table.Cell>{project.tags.join(", ")}</Table.Cell>
							<Table.Cell>
								<ProjectStatusBadge status={project.status} />
							</Table.Cell>
							<Table.Cell>
								<time
									dateTime={project.last_activity_at}
									title={fullDate(project.last_activity_at)}
								>
									{relativeTime(project.last_activity_at)}
								</time>
							</Table.Cell>
							<Table.Cell>{project.members.length}</Table.Cell>
							<Table.Cell>
								{project.relevant_papers === null
									? "—"
									: `${project.relevant_papers} of ${project.papers_found ?? 0}`}
							</Table.Cell>
							<Table.Cell>
								{project.brief_version === null
									? "—"
									: `v${project.brief_version}`}
							</Table.Cell>
						</Table.Row>
					))}
				</Table.Body>
			</Table.Root>
		</Table.ScrollArea>
	);
}

function Group({
	group,
	isGrouped,
	children,
}: {
	group: ProjectGroup;
	isGrouped: boolean;
	children: ReactNode;
}) {
	const id = useId();
	const [isOpen, setOpen] = useState(true);
	if (!isGrouped) {
		return <>{children}</>;
	}
	return (
		<section aria-labelledby={`${id}-heading`}>
			<chakra.h2 id={`${id}-heading`} m="0" mb="2">
				<DisclosureButton
					isOpen={isOpen}
					controls={`${id}-body`}
					onToggle={() => setOpen(!isOpen)}
				>
					{group.label} · {plural(group.projects.length, "project")}
				</DisclosureButton>
			</chakra.h2>
			<Box id={`${id}-body`}>{isOpen ? <Reveal>{children}</Reveal> : null}</Box>
		</section>
	);
}

function ViewToggle({
	view,
	onChange,
}: {
	view: ListView;
	onChange: (view: ListView) => void;
}) {
	return (
		<Flex role="group" aria-label="View" gap="1">
			<IconButton
				size="sm"
				variant={view === "cards" ? "subtle" : "ghost"}
				rounded="card"
				aria-label="Cards"
				aria-pressed={view === "cards"}
				onClick={() => onChange("cards")}
			>
				<LayoutGrid />
			</IconButton>
			<IconButton
				size="sm"
				variant={view === "list" ? "subtle" : "ghost"}
				rounded="card"
				aria-label="List"
				aria-pressed={view === "list"}
				onClick={() => onChange("list")}
			>
				<List />
			</IconButton>
		</Flex>
	);
}

/** Empty lists in the URL mean "any", so they are left out of it. */
function tidy(changes: Partial<ProjectsSearch>): Partial<ProjectsSearch> {
	return {
		...changes,
		...(changes.status?.length === 0 ? { status: undefined } : {}),
		...(changes.tag?.length === 0 ? { tag: undefined } : {}),
	};
}

export function ProjectsPage() {
	const api = useApi();
	const search = useSearch({ from: "/signed-in/projects" });
	const navigate = useNavigate({ from: "/projects" });
	const projects = useQuery(filteredProjectsQuery(api, filtersFor(search)));
	const everything = useQuery(projectListQuery(api, true));
	const collections = useQuery(collectionsQuery(api));
	const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
	const [outcome, setOutcome] = useState("");

	const view: ListView = search.view ?? "cards";
	const groupBy: GroupBy = search.group ?? "collection";
	const names = new Map((collections.data ?? []).map((c) => [c.id, c.name]));
	const change = (changes: Partial<ProjectsSearch>) =>
		void navigate({
			search: (previous) => ({ ...previous, ...tidy(changes) }),
			replace: true,
		});
	const select = (projectId: string, isSelected: boolean) => {
		const next = new Set(selected);
		if (isSelected) {
			next.add(projectId);
		} else {
			next.delete(projectId);
		}
		setSelected(next);
	};
	const groups =
		projects.data === undefined
			? []
			: groupProjects(projects.data, groupBy, names);
	const isFiltered = Object.entries(search).some(
		([key, value]) =>
			value !== undefined && !["view", "group", "sort"].includes(key),
	);

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
			<Stack gap="4">
				<ProjectFilters
					search={search}
					onChange={change}
					everything={everything.data ?? []}
					collectionName={
						search.collection === undefined
							? undefined
							: names.get(search.collection)
					}
				/>
				<Flex gap="4" wrap="wrap" align="center" justify="space-between">
					<Flex gap="4" wrap="wrap">
						<LabelledSelect
							label="Group by"
							value={groupBy}
							options={GROUPS}
							onChange={(value) =>
								change({
									group: GROUPS.find(([g]) => g === value)?.[0],
								})
							}
						/>
						<LabelledSelect
							label="Sort"
							value={search.sort ?? "last_activity"}
							options={SORTS}
							onChange={(value) =>
								change({
									sort: SORTS.find(([s]) => s === value)?.[0],
								})
							}
						/>
					</Flex>
					<ViewToggle
						view={view}
						onChange={(next) =>
							change({ view: next === "cards" ? undefined : next })
						}
					/>
				</Flex>
			</Stack>
			<Text role="status" fontSize="sm">
				{outcome}
			</Text>
			{view === "list" && selected.size > 0 ? (
				<BulkActions
					selected={[...selected]}
					onDone={(message) => {
						setSelected(new Set());
						setOutcome(message);
					}}
				/>
			) : null}
			{projects.isPending ? <Loading what="projects" /> : null}
			{projects.isError ? (
				<ErrorNotice
					error={projects.error}
					onRetry={() => void projects.refetch()}
				/>
			) : null}
			{projects.isSuccess && projects.data.length === 0 ? (
				<Text color="fg.muted">
					{isFiltered
						? "No projects match these filters."
						: "You have no projects yet."}
				</Text>
			) : null}
			<Stack gap="8">
				{groups.map((group) => (
					<Group key={group.key} group={group} isGrouped={groupBy !== "none"}>
						{view === "cards" ? (
							<Cards placed={group.projects} names={names} />
						) : (
							<Rows
								caption={groupBy === "none" ? "Your projects" : group.label}
								placed={group.projects}
								names={names}
								selected={selected}
								onSelect={select}
							/>
						)}
					</Group>
				))}
			</Stack>
		</Page>
	);
}
