/**
 * The left rail (UI_UX_SPEC §4.1): where to go, recent projects, and the
 * person's own controls. Needs-you, pinned projects and collections come with
 * the API that serves them (§15, P3).
 */

import { Box, chakra, Flex, IconButton, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { House, LayoutGrid, LogOut, PanelLeft, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { unwrapEmpty } from "../api/client";
import { useApi } from "../app/api-context";
import { projectListQuery, sessionQuery } from "../app/queries";
import { initials } from "../ui/initials";
import { ProjectBadge } from "../ui/ProjectBadge";
import { rail, railItem } from "./railStyles";
import { ThemeButton } from "./ThemeButton";

const RECENT_COUNT = 5;

interface RailProps {
	isFolded: boolean;
	isDrawerOpen: boolean;
	onToggleFold: () => void;
	onNavigate: () => void;
}

/** A router link with the rail item's look; `asChild` keeps the router's own <a>. */
function RailLink({ children }: { children: ReactNode }) {
	return (
		<Box asChild className="rail-item" css={railItem}>
			{children}
		</Box>
	);
}

export function Rail({
	isFolded,
	isDrawerOpen,
	onToggleFold,
	onNavigate,
}: RailProps) {
	const api = useApi();
	const queryClient = useQueryClient();
	const navigate = useNavigate();
	const session = useQuery(sessionQuery(api));
	const projects = useQuery(projectListQuery(api));
	const recent = [...(projects.data ?? [])]
		.sort((a, b) => b.created_at.localeCompare(a.created_at))
		.slice(0, RECENT_COUNT);

	const signOut = useMutation({
		mutationFn: () => unwrapEmpty(api.POST("/api/v1/auth/logout")),
		// Leave either way: a sign-out that failed because the session had
		// already ended still ends at the sign-in page.
		onSettled: async () => {
			queryClient.clear();
			await navigate({ to: "/sign-in" });
		},
	});

	const classes = [isFolded ? "is-folded" : "", isDrawerOpen ? "is-open" : ""]
		.filter(Boolean)
		.join(" ");
	const name = session.data?.user.display_name ?? "";

	return (
		<chakra.nav id="rail" className={classes} aria-label="Main" css={rail}>
			<Flex justify="flex-end" pb="2">
				<IconButton
					className="rail-fold"
					variant="ghost"
					size="sm"
					rounded="card"
					aria-label="Fold the sidebar"
					aria-expanded={!isFolded}
					aria-controls="rail"
					title="Fold the sidebar ( [ )"
					onClick={onToggleFold}
				>
					<PanelLeft />
				</IconButton>
			</Flex>
			<chakra.ul listStyle="none" m="0" p="0">
				<li>
					<RailLink>
						<Link to="/" activeOptions={{ exact: true }} onClick={onNavigate}>
							<House size={18} />
							<span className="rail-label">Home</span>
						</Link>
					</RailLink>
				</li>
				<li>
					<RailLink>
						<Link
							to="/projects"
							activeOptions={{ exact: true }}
							onClick={onNavigate}
						>
							<LayoutGrid size={18} />
							<span className="rail-label">All projects</span>
						</Link>
					</RailLink>
				</li>
			</chakra.ul>
			{recent.length === 0 ? null : (
				<>
					<Text
						as="h2"
						className="rail-section"
						px="3"
						pt="5"
						pb="2"
						fontSize="xs"
						textTransform="uppercase"
						letterSpacing="0.08em"
						color="fg.muted"
					>
						Recent
					</Text>
					<chakra.ul listStyle="none" m="0" p="0">
						{recent.map((project) => (
							<li key={project.id}>
								<RailLink>
									<Link
										to="/p/$projectId"
										params={{ projectId: project.id }}
										title={project.name}
										onClick={onNavigate}
									>
										<ProjectBadge name={project.name} />
										<Text
											as="span"
											className="rail-label"
											flex="1"
											minW="0"
											truncate
										>
											{project.name}
										</Text>
									</Link>
								</RailLink>
							</li>
						))}
					</chakra.ul>
				</>
			)}
			<Flex
				direction="column"
				gap="1"
				mt="auto"
				pt="4"
				borderTopWidth="1px"
				borderColor="border"
			>
				<RailLink>
					<Link to="/projects/new" onClick={onNavigate}>
						<Plus size={18} />
						<span className="rail-label">New project</span>
					</Link>
				</RailLink>
				<ThemeButton className="rail-item" css={railItem} />
				<RailLink>
					<Link to="/settings" onClick={onNavigate}>
						<Flex
							aria-hidden="true"
							boxSize="7"
							flex="none"
							rounded="full"
							align="center"
							justify="center"
							bg="brand.solid"
							color="brand.contrast"
							fontSize="xs"
							fontWeight="semibold"
						>
							{initials(name)}
						</Flex>
						<Text as="span" className="rail-label" flex="1" minW="0" truncate>
							{name}
						</Text>
						<chakra.span srOnly>, account</chakra.span>
					</Link>
				</RailLink>
				<chakra.button
					type="button"
					className="rail-item"
					css={railItem}
					onClick={() => signOut.mutate()}
				>
					<LogOut size={18} />
					<span className="rail-label">Sign out</span>
				</chakra.button>
			</Flex>
		</chakra.nav>
	);
}
