/**
 * The signed-in frame (UI_UX_SPEC §4, §6.1): the header with the two logo
 * slots, the rail, the main area, the footer, and the banner that says the
 * session has ended. Breakpoints are CSS (railStyles.ts); the rail is a drawer
 * below 1024 px, which is the only layout state kept here.
 */

import {
	Alert,
	Box,
	Link as ChakraLink,
	chakra,
	Flex,
	IconButton,
} from "@chakra-ui/react";
import { Link, Outlet, useLocation } from "@tanstack/react-router";
import { Menu, TriangleAlert } from "lucide-react";
import { useCallback, useState } from "react";
import { useSignedOutNotice } from "../app/memory";
import { useRailFolded } from "../app/preferences";
import { LabMark, ProjectMark } from "../ui/Brand";
import { SiteFooter } from "../ui/SiteFooter";
import { SkipLink } from "../ui/SkipLink";
import { Rail } from "./Rail";
import { COMPACT_ZOOM, HEADER_HEIGHT } from "./railStyles";
import { useShortcuts } from "./useShortcuts";

function SignedOutBanner() {
	const [isSignedOut] = useSignedOutNotice();
	const location = useLocation();
	if (!isSignedOut) {
		return null;
	}
	return (
		<Alert.Root
			role="alert"
			colorPalette="caveat"
			variant="subtle"
			rounded="0"
			px="4"
			py="3"
		>
			<Alert.Indicator>
				<TriangleAlert />
			</Alert.Indicator>
			<Alert.Description color="caveat.fg">
				You've been signed out. Sign in again to continue.{" "}
				<ChakraLink
					asChild
					color="inherit"
					textDecoration="underline"
					fontWeight="semibold"
				>
					<Link to="/sign-in" search={{ redirect: location.href }}>
						Sign in again
					</Link>
				</ChakraLink>
			</Alert.Description>
		</Alert.Root>
	);
}

interface HeaderProps {
	isDrawerOpen: boolean;
	onOpenMenu: () => void;
}

/** After LiGHT's site header: the project's mark on the left, the lab's on the right. */
function Header({ isDrawerOpen, onOpenMenu }: HeaderProps) {
	return (
		<Flex
			as="header"
			flex="none"
			zIndex="20"
			h={HEADER_HEIGHT}
			px={{ base: "4", md: "6" }}
			gap="3"
			align="center"
			justify="space-between"
			bg="bg"
			borderBottomWidth="1px"
			borderColor="border"
		>
			<Flex align="center" gap="2">
				<IconButton
					display={{ base: "inline-flex", lg: "none" }}
					variant="ghost"
					rounded="card"
					aria-label="Open the menu"
					aria-controls="rail"
					aria-expanded={isDrawerOpen}
					onClick={onOpenMenu}
				>
					<Menu />
				</IconButton>
				<ChakraLink
					asChild
					color="fg"
					textDecoration="none"
					p="1"
					rounded="card"
				>
					<Link to="/" aria-label="Humani-T, home">
						<ProjectMark size="xl" />
					</Link>
				</ChakraLink>
			</Flex>
			<LabMark />
		</Flex>
	);
}

export function AppShell() {
	const [isFolded, setFolded] = useRailFolded();
	const [isDrawerOpen, setDrawerOpen] = useState(false);
	const location = useLocation();
	const toggleRail = useCallback(
		() => setFolded(!isFolded),
		[isFolded, setFolded],
	);
	const closeDrawer = useCallback(() => setDrawerOpen(false), []);
	useShortcuts({ toggleRail, closeDrawer });
	// The workspace fills the screen down to its composer; a footer would push it off.
	const isWorkspace = location.pathname.startsWith("/p/");

	return (
		// One zoom for the signed-in app (COMPACT_ZOOM). Heights are percentages of
		// the window, never 100vh: zoom scales viewport units too, which would leave
		// a gap at the bottom. The page itself never scrolls; its content area does.
		<Flex
			direction="column"
			h="100%"
			overflow="hidden"
			css={{ zoom: COMPACT_ZOOM }}
		>
			<SkipLink href="#main">Skip to main content</SkipLink>
			<Header
				isDrawerOpen={isDrawerOpen}
				onOpenMenu={() => setDrawerOpen(true)}
			/>
			<Flex flex="1" minH="0">
				<Rail
					isFolded={isFolded}
					isDrawerOpen={isDrawerOpen}
					onToggleFold={toggleRail}
					onNavigate={closeDrawer}
				/>
				{isDrawerOpen ? (
					<chakra.button
						type="button"
						position="fixed"
						inset="0"
						zIndex="30"
						bg="scrim"
						border="0"
						p="0"
						tabIndex={-1}
						aria-label="Close the menu"
						onClick={closeDrawer}
					/>
				) : null}
				<Flex
					direction="column"
					flex="1"
					minW="0"
					position="relative"
					overflowY="auto"
				>
					<SignedOutBanner />
					{/* A workspace is exactly as tall as the window, and its chat and canvas
					    scroll inside it. Without minH 0 it grows to fit a long table, and
					    the whole workspace scrolls, composer included. Other pages grow,
					    and this column scrolls them. */}
					<Box
						as="main"
						id="main"
						tabIndex={-1}
						flex="1"
						display="flex"
						flexDirection="column"
						minH={isWorkspace ? "0" : undefined}
						overflow={isWorkspace ? "hidden" : undefined}
					>
						<Outlet />
					</Box>
					{isWorkspace ? null : <SiteFooter />}
				</Flex>
			</Flex>
		</Flex>
	);
}
