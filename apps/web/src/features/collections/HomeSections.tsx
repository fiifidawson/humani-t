/**
 * Home's "Pinned" and "Collections" (UI_UX_SPEC §7.1). Both read the project
 * list and the collections the API returns; neither shows anything when empty.
 */

import { Link as ChakraLink, chakra, Flex, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useApi } from "../../app/api-context";
import { collectionsQuery } from "../../app/queries";
import { linkStyle } from "../../ui/LinkButton";
import { SectionHeading } from "../../ui/SectionHeading";
import type { Collection, ProjectSummary } from "../projects/format";
import { ProjectSummaryCard } from "../projects/ProjectSummaryCard";

const PINNED_ON_HOME = 8;

/** The person's pins, newest first: up to 8, in one row that scrolls sideways. */
export function PinnedProjects({
	projects,
}: {
	projects: readonly ProjectSummary[];
}) {
	const api = useApi();
	const collections = useQuery(collectionsQuery(api));
	const names = new Map((collections.data ?? []).map((c) => [c.id, c.name]));
	const pinned = projects
		.filter((p) => p.pinned_at !== null)
		.sort((a, b) => (b.pinned_at ?? "").localeCompare(a.pinned_at ?? ""))
		.slice(0, PINNED_ON_HOME);
	if (pinned.length === 0) {
		return null;
	}
	return (
		<section aria-labelledby="pinned-heading">
			<SectionHeading id="pinned-heading">Pinned</SectionHeading>
			<Flex
				as="ul"
				listStyle="none"
				m="0"
				p="0"
				pb="2"
				gap="4"
				overflowX="auto"
				// Scrollable regions must be reachable by keyboard (axe: scrollable-region-focusable).
				tabIndex={0}
				aria-label="Pinned projects"
			>
				{pinned.map((project) => (
					<chakra.li key={project.id} flex="none" w="18rem">
						<ProjectSummaryCard
							project={project}
							collectionName={
								project.collection_id === null
									? undefined
									: names.get(project.collection_id)
							}
						/>
					</chakra.li>
				))}
			</Flex>
		</section>
	);
}

/** "Sahel 2026 (4) · Protection (3) · Not in a collection (5)", each a link to the filtered list. */
export function CollectionsRow({
	projects,
}: {
	projects: readonly ProjectSummary[];
}) {
	const api = useApi();
	const collections = useQuery(collectionsQuery(api));
	const filed: Collection[] = (collections.data ?? []).filter(
		(c) => c.project_count > 0,
	);
	if (filed.length === 0) {
		return null;
	}
	const unfiled = projects.filter((p) => p.collection_id === null).length;
	return (
		<section aria-labelledby="collections-heading">
			<SectionHeading id="collections-heading">Collections</SectionHeading>
			<Flex as="ul" listStyle="none" m="0" p="0" gap="2" wrap="wrap">
				{filed.map((collection) => (
					<chakra.li key={collection.id} display="flex" gap="2">
						<ChakraLink asChild css={linkStyle}>
							<Link to="/projects" search={{ collection: collection.id }}>
								{collection.name} ({collection.project_count})
							</Link>
						</ChakraLink>
						<Text as="span" aria-hidden="true" color="fg.muted">
							·
						</Text>
					</chakra.li>
				))}
				<li>
					<ChakraLink asChild css={linkStyle}>
						<Link to="/projects" search={{ collection: "none" }}>
							Not in a collection ({unfiled})
						</Link>
					</ChakraLink>
				</li>
			</Flex>
		</section>
	);
}
