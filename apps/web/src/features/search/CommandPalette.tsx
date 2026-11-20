/**
 * Search and commands (UI_UX_SPEC §7.6), opened with Ctrl K or ⌘K. A combobox
 * over a listbox: ↑↓ move, Enter opens, Tab changes the scope, Esc closes.
 * The API searches and decides what the person may see; this only shows it.
 * The typed text stays in memory, never in the browser's storage.
 */

import {
	Box,
	Dialog as ChakraDialog,
	chakra,
	Flex,
	Kbd,
	Portal,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
	CircleHelp,
	Diamond,
	FileText,
	Folder,
	Hand,
	MessageSquareText,
	Moon,
	Plus,
	Quote,
	Search,
	Settings,
} from "lucide-react";
import {
	type KeyboardEvent,
	type ReactNode,
	useEffect,
	useId,
	useRef,
	useState,
} from "react";
import { unwrap } from "../../api/client";
import { ApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { useTheme } from "../../app/preferences";
import { projectListQuery } from "../../app/queries";
import { COMPACT_ZOOM } from "../../shell/railStyles";
import { useNeedsYouItems } from "./needsYou";

type Scope = components["schemas"]["SearchScope"];
type Hit = components["schemas"]["SearchHitOut"];

const SCOPES: readonly { value: Scope | null; label: string }[] = [
	{ value: null, label: "All" },
	{ value: "projects", label: "Projects" },
	{ value: "briefs", label: "Briefs" },
	{ value: "papers", label: "Papers" },
	{ value: "passages", label: "Passages" },
	{ value: "questions", label: "Questions" },
];

const MIN_QUERY = 2;
const RECENT_COUNT = 5;
const DEBOUNCE_MS = 250;

interface Entry {
	id: string;
	/** What it is, in a word or two: "Brief v2", "P0012"; none for a command. */
	kind?: string;
	label: string;
	detail?: string | undefined;
	icon: ReactNode;
	run: () => void;
}

interface Group {
	id: string;
	label: string;
	entries: Entry[];
}

function useDebounced(value: string, delay: number): string {
	const [settled, setSettled] = useState(value);
	useEffect(() => {
		const timer = window.setTimeout(() => setSettled(value), delay);
		return () => window.clearTimeout(timer);
	}, [value, delay]);
	return settled;
}

function matchesWords(query: string, text: string): boolean {
	const folded = text.toLowerCase();
	return query
		.toLowerCase()
		.split(/\s+/)
		.every((word) => folded.includes(word));
}

type Navigate = ReturnType<typeof useNavigate>;

/** Where a hit leads, by what it is; a missing id falls back to its project. */
function openHit(navigate: Navigate, projectId: string, hit: Hit): void {
	const reviewId = hit.review_id;
	const run = hit.run_id ?? undefined;
	if (hit.scope === "questions" && hit.thread_id !== null) {
		void navigate({
			to: "/p/$projectId/ask/$threadId",
			params: { projectId, threadId: hit.thread_id },
		});
	} else if (reviewId === null) {
		void navigate({ to: "/p/$projectId", params: { projectId } });
	} else if (hit.scope === "briefs" || hit.scope === "passages") {
		void navigate({
			to: "/p/$projectId/r/$reviewId/brief",
			params: { projectId, reviewId },
			search: { run, source: hit.tag ?? undefined },
		});
	} else if (hit.scope === "papers") {
		void navigate({
			to: "/p/$projectId/r/$reviewId/papers",
			params: { projectId, reviewId },
			search: { run },
		});
	} else {
		void navigate({
			to: "/p/$projectId/r/$reviewId",
			params: { projectId, reviewId },
		});
	}
}

function describeHit(
	hit: Hit,
): Pick<Entry, "kind" | "label" | "detail" | "icon"> {
	switch (hit.scope) {
		case "briefs":
			return {
				kind: `Brief v${hit.brief_version ?? ""}`.trim(),
				label: hit.text,
				icon: <Diamond size={16} />,
			};
		case "papers":
			return {
				kind: hit.tag ?? "Paper",
				label: hit.title ?? hit.text,
				detail:
					hit.title !== null && hit.title !== hit.text ? hit.text : undefined,
				icon: <FileText size={16} />,
			};
		case "passages":
			return {
				kind: `Passage ${hit.tag ?? ""}`.trim(),
				label: hit.text,
				icon: <Quote size={16} />,
			};
		case "questions":
			return {
				kind: "Question",
				label: hit.text,
				icon: <CircleHelp size={16} />,
			};
		default:
			return hit.review_id === null
				? { kind: "Project", label: hit.text, icon: <Folder size={16} /> }
				: {
						kind: "Review",
						label: hit.text,
						icon: <MessageSquareText size={16} />,
					};
	}
}

function Option({
	entry,
	id,
	isActive,
	onPoint,
	onChoose,
}: {
	entry: Entry;
	id: string;
	isActive: boolean;
	onPoint: () => void;
	onChoose: () => void;
}) {
	return (
		<Flex
			id={id}
			role="option"
			aria-selected={isActive}
			align="flex-start"
			gap="3"
			px="3"
			py="2"
			rounded="card"
			cursor="pointer"
			bg={isActive ? "bg.emphasized" : undefined}
			onMouseMove={onPoint}
			// Keep the focus in the box: the listbox is driven from it.
			onMouseDown={(event) => event.preventDefault()}
			onClick={onChoose}
		>
			<Box as="span" color="fg.muted" mt="0.5" flex="none" aria-hidden="true">
				{entry.icon}
			</Box>
			<Box flex="1" minW="0">
				{entry.kind === undefined ? null : (
					<>
						<Text as="span" fontSize="xs" color="fg.muted" me="2">
							{entry.kind}
						</Text>{" "}
					</>
				)}
				<Text as="span" fontSize="sm">
					{entry.label}
				</Text>
				{entry.detail === undefined ? null : (
					<Text fontSize="xs" color="fg.muted" lineClamp={2}>
						{entry.detail}
					</Text>
				)}
			</Box>
		</Flex>
	);
}

function Palette({ onClose }: { onClose: () => void }) {
	const api = useApi();
	const navigate = useNavigate();
	const [, setTheme] = useTheme();
	const baseId = useId();
	const inputId = `${baseId}-input`;
	const listId = `${baseId}-list`;
	const hintId = `${baseId}-hint`;
	const [text, setText] = useState("");
	const [scopeIndex, setScopeIndex] = useState(0);
	const [activeId, setActiveId] = useState<string | null>(null);
	const input = useRef<HTMLInputElement>(null);
	const query = text.trim();
	const settled = useDebounced(query, DEBOUNCE_MS);
	const scope = SCOPES[scopeIndex]?.value ?? null;
	const needsYou = useNeedsYouItems();
	const projects = useQuery(projectListQuery(api));
	const search = useQuery({
		queryKey: ["search", settled, scope],
		queryFn: () =>
			unwrap(
				api.GET("/api/v1/search", {
					params: {
						query: scope === null ? { q: settled } : { q: settled, scope },
					},
				}),
			),
		enabled: settled.length >= MIN_QUERY,
		staleTime: 30_000,
		retry: false,
	});

	const choose = (go: () => void) => () => {
		onClose();
		go();
	};

	const commands: Entry[] = [
		{
			id: "command-new-project",
			label: "New project",
			icon: <Plus size={16} />,
			run: choose(() => void navigate({ to: "/projects/new" })),
		},
		{
			id: "command-settings",
			label: "Go to settings",
			icon: <Settings size={16} />,
			run: choose(() => void navigate({ to: "/settings" })),
		},
		{
			id: "command-dark-mode",
			label: "Toggle dark mode",
			icon: <Moon size={16} />,
			run: choose(() =>
				setTheme(
					document.documentElement.classList.contains("dark")
						? "light"
						: "dark",
				),
			),
		},
	];

	const groups: Group[] = [];
	if (query === "") {
		groups.push({
			id: "needs-you",
			label: "Needs you",
			entries: needsYou.map((item) => ({
				id: `needs-you-${item.id}`,
				kind: "Needs you",
				label: item.label,
				icon: <Hand size={16} />,
				run: choose(
					() =>
						void navigate({
							to: "/p/$projectId",
							params: { projectId: item.projectId },
						}),
				),
			})),
		});
		groups.push({
			id: "recent",
			label: "Recent projects",
			entries: [...(projects.data ?? [])]
				.sort((a, b) => b.created_at.localeCompare(a.created_at))
				.slice(0, RECENT_COUNT)
				.map((project) => ({
					id: `recent-${project.id}`,
					kind: "Project",
					label: project.name,
					icon: <Folder size={16} />,
					run: choose(
						() =>
							void navigate({
								to: "/p/$projectId",
								params: { projectId: project.id },
							}),
					),
				})),
		});
		groups.push({ id: "commands", label: "Commands", entries: commands });
	} else {
		groups.push({
			id: "commands",
			label: "Commands",
			entries: commands.filter((command) => matchesWords(query, command.label)),
		});
		const isCurrent = search.data !== undefined && settled === query;
		for (const group of isCurrent ? (search.data?.groups ?? []) : []) {
			groups.push({
				id: `project-${group.project_id}`,
				label: group.is_archived
					? `${group.project_name} (archived)`
					: group.project_name,
				entries: group.hits.map((hit, n) => ({
					id: `hit-${group.project_id}-${n}`,
					...describeHit(hit),
					run: choose(() => openHit(navigate, group.project_id, hit)),
				})),
			});
		}
	}
	const shown = groups.filter((group) => group.entries.length > 0);
	const entries = shown.flatMap((group) => group.entries);
	// The pointed-at entry while it is still listed, else the first.
	const activeIndex = Math.max(
		entries.findIndex((entry) => entry.id === activeId),
		0,
	);
	const activeEntry = entries[activeIndex];
	const optionId = (entry: Entry) => `${baseId}-${entry.id}`;
	const activeOptionId =
		activeEntry === undefined ? undefined : optionId(activeEntry);

	useEffect(() => {
		if (activeOptionId !== undefined) {
			document
				.getElementById(activeOptionId)
				?.scrollIntoView?.({ block: "nearest" });
		}
	}, [activeOptionId]);

	const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
		if (event.key === "ArrowDown" || event.key === "ArrowUp") {
			event.preventDefault();
			if (entries.length > 0) {
				const step = event.key === "ArrowDown" ? 1 : -1;
				const next =
					entries[(activeIndex + step + entries.length) % entries.length];
				setActiveId(next?.id ?? null);
			}
		} else if (event.key === "Enter") {
			event.preventDefault();
			activeEntry?.run();
		}
	};

	useEffect(() => {
		input.current?.focus();
		// Tab changes the scope (§7.6). The dialog's focus trap listens on the
		// document, so this listens before it, on the window, in capture.
		const onTab = (event: globalThis.KeyboardEvent) => {
			if (event.key === "Tab" && event.target === input.current) {
				event.preventDefault();
				event.stopPropagation();
				const step = event.shiftKey ? -1 : 1;
				setScopeIndex((n) => (n + step + SCOPES.length) % SCOPES.length);
			}
		};
		window.addEventListener("keydown", onTab, true);
		return () => window.removeEventListener("keydown", onTab, true);
	}, []);

	let status = "";
	if (query.length === 1) {
		status = "Type at least two letters to search.";
	} else if (query.length >= MIN_QUERY) {
		if (search.isError) {
			status =
				search.error instanceof ApiError
					? userMessage(search.error)
					: "The search didn't work. Try again.";
		} else if (search.isFetching || settled !== query) {
			status = "Searching…";
		} else if (search.data?.groups.length === 0) {
			status = `No results for “${query}”.`;
		} else if (search.data?.is_truncated) {
			status =
				"Showing the first results only. Add words to narrow the search.";
		}
	}

	return (
		<>
			<Flex align="center" gap="3" px="4" py="3" borderBottomWidth="1px">
				<Box as="span" color="fg.muted" aria-hidden="true">
					<Search size={18} />
				</Box>
				<chakra.input
					ref={input}
					id={inputId}
					data-palette-input
					flex="1"
					minW="0"
					bg="transparent"
					border="0"
					fontSize="md"
					placeholder="Search projects, briefs, papers… or type a command"
					role="combobox"
					aria-label="Search or type a command"
					aria-expanded={entries.length > 0}
					aria-controls={listId}
					aria-autocomplete="list"
					aria-activedescendant={activeOptionId}
					aria-describedby={hintId}
					autoComplete="off"
					spellCheck={false}
					value={text}
					onChange={(event) => setText(event.target.value)}
					onKeyDown={onKeyDown}
				/>
				<Kbd aria-hidden="true">Esc</Kbd>
			</Flex>
			<Flex
				role="group"
				aria-label="Search in"
				gap="1"
				px="4"
				py="2"
				wrap="wrap"
				borderBottomWidth="1px"
			>
				{SCOPES.map((option, n) => (
					<chakra.button
						key={option.label}
						type="button"
						tabIndex={-1}
						aria-pressed={n === scopeIndex}
						px="3"
						minH="7"
						rounded="full"
						fontSize="sm"
						borderWidth="1px"
						borderColor={n === scopeIndex ? "brand.solid" : "border"}
						bg={n === scopeIndex ? "brand.subtle" : "transparent"}
						fontWeight={n === scopeIndex ? "semibold" : "normal"}
						onMouseDown={(event) => event.preventDefault()}
						onClick={() => setScopeIndex(n)}
					>
						{option.label}
					</chakra.button>
				))}
			</Flex>
			<Box
				id={listId}
				role="listbox"
				aria-label="Results"
				maxH="60vh"
				overflowY="auto"
				px="2"
				py="2"
			>
				{shown.map((group) => (
					<Box
						key={group.id}
						role="group"
						aria-labelledby={`${baseId}-${group.id}`}
						pb="2"
					>
						<Text
							id={`${baseId}-${group.id}`}
							role="presentation"
							px="3"
							pt="2"
							pb="1"
							fontSize="xs"
							textTransform="uppercase"
							letterSpacing="0.08em"
							color="fg.muted"
						>
							{group.label}
						</Text>
						{group.entries.map((entry) => (
							<Option
								key={entry.id}
								entry={entry}
								id={optionId(entry)}
								isActive={entry === activeEntry}
								onPoint={() => setActiveId(entry.id)}
								onChoose={entry.run}
							/>
						))}
					</Box>
				))}
			</Box>
			<Flex
				justify="space-between"
				gap="3"
				wrap="wrap"
				px="4"
				py="2"
				borderTopWidth="1px"
				fontSize="xs"
				color="fg.muted"
			>
				<Text role="status">{status}</Text>
				<Text id={hintId}>
					↑↓ to move · Enter to open · Tab to change scope
				</Text>
			</Flex>
		</>
	);
}

export function CommandPalette({
	isOpen,
	onClose,
}: {
	isOpen: boolean;
	onClose: () => void;
}) {
	return (
		<ChakraDialog.Root
			open={isOpen}
			onOpenChange={(details) => {
				if (!details.open) {
					onClose();
				}
			}}
			placement="top"
			lazyMount
			unmountOnExit
			initialFocusEl={() =>
				document.querySelector<HTMLElement>("[data-palette-input]")
			}
		>
			<Portal>
				<ChakraDialog.Backdrop bg="scrim" />
				{/* Outside AppShell's zoom, as ui/Dialog.tsx explains. */}
				<ChakraDialog.Positioner px="4" css={{ zoom: COMPACT_ZOOM }}>
					<ChakraDialog.Content
						bg="bg.panel"
						rounded="card"
						maxW="2xl"
						w="full"
						overflow="hidden"
					>
						<ChakraDialog.Title srOnly>Search and commands</ChakraDialog.Title>
						<Palette onClose={onClose} />
					</ChakraDialog.Content>
				</ChakraDialog.Positioner>
			</Portal>
		</ChakraDialog.Root>
	);
}
