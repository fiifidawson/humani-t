/**
 * Collections in the rail (UI_UX_SPEC §7.3): each folds and unfolds to show its
 * projects, remembers that per browser, and shows how many of the person's
 * projects it holds. Hidden while the rail is folded to icons.
 */

import { Box, chakra, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useApi } from "../../app/api-context";
import { useOpenCollections } from "../../app/preferences";
import { collectionsQuery, projectListQuery } from "../../app/queries";
import { railItem } from "../../shell/railStyles";
import { ProjectBadge } from "../../ui/ProjectBadge";
import { plural } from "../reviews/format";

export function RailCollections({ onNavigate }: { onNavigate: () => void }) {
	const api = useApi();
	const collections = useQuery(collectionsQuery(api));
	const projects = useQuery(projectListQuery(api));
	const [open, setOpen] = useOpenCollections();
	const filed = (collections.data ?? []).filter((c) => c.project_count > 0);
	if (filed.length === 0) {
		return null;
	}
	return (
		<Box className="rail-section">
			<Text
				as="h2"
				px="3"
				pt="5"
				pb="2"
				fontSize="xs"
				textTransform="uppercase"
				letterSpacing="0.08em"
				color="fg.muted"
			>
				Collections
			</Text>
			<chakra.ul listStyle="none" m="0" p="0">
				{filed.map((collection) => {
					const isOpen = open.has(collection.id);
					const bodyId = `rail-collection-${collection.id}`;
					const inside = (projects.data ?? []).filter(
						(p) => p.collection_id === collection.id,
					);
					return (
						<li key={collection.id}>
							<chakra.button
								type="button"
								className="rail-item"
								css={railItem}
								aria-expanded={isOpen}
								aria-controls={bodyId}
								onClick={() => setOpen(collection.id, !isOpen)}
							>
								<chakra.span
									display="inline-flex"
									transition="transform 0.15s ease"
									transform={isOpen ? "rotate(90deg)" : "none"}
								>
									<ChevronRight size={16} />
								</chakra.span>
								<Text as="span" flex="1" minW="0" truncate>
									{collection.name}
								</Text>
								<Text as="span" fontSize="sm" color="fg.muted">
									<span aria-hidden="true">{collection.project_count}</span>
									<chakra.span srOnly>
										, {plural(collection.project_count, "project")}
									</chakra.span>
								</Text>
							</chakra.button>
							{isOpen ? (
								<chakra.ul id={bodyId} listStyle="none" m="0" p="0" pl="4">
									{inside.map((project) => (
										<li key={project.id}>
											<Box asChild className="rail-item" css={railItem}>
												<Link
													to="/p/$projectId"
													params={{ projectId: project.id }}
													title={project.name}
													onClick={onNavigate}
												>
													<ProjectBadge name={project.name} />
													<Text as="span" flex="1" minW="0" truncate>
														{project.name}
													</Text>
												</Link>
											</Box>
										</li>
									))}
								</chakra.ul>
							) : null}
						</li>
					);
				})}
			</chakra.ul>
		</Box>
	);
}
