/**
 * The project's ⋯ menu in its header: pin it to Home, and for owners, "Move to
 * collection…", the keyboard way to file it (UI_UX_SPEC §7.3).
 *
 * A disclosure of plain buttons rather than an ARIA menu: Tab and Enter work as
 * everywhere else, and Esc closes it.
 */

import { Badge, Box, Button, IconButton, Stack } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Ellipsis, FolderClosed, FolderInput, Pin, PinOff } from "lucide-react";
import { useId, useRef, useState } from "react";
import { useApi } from "../../app/api-context";
import { collectionsQuery } from "../../app/queries";
import type { Project } from "../projects/format";
import { MoveToCollection } from "./MoveToCollection";
import { usePin } from "./organise";

export function ProjectMenu({ project }: { project: Project }) {
	const id = useId();
	const pin = usePin(project.id);
	const toggle = useRef<HTMLButtonElement>(null);
	const [isOpen, setOpen] = useState(false);
	const [isMoving, setMoving] = useState(false);
	const close = () => {
		setOpen(false);
		toggle.current?.focus();
	};
	return (
		<Box
			position="relative"
			flex="none"
			onKeyDown={(event) => {
				if (event.key === "Escape" && isOpen) {
					close();
				}
			}}
		>
			<IconButton
				ref={toggle}
				variant="ghost"
				size="sm"
				rounded="card"
				aria-label="More project actions"
				aria-expanded={isOpen}
				aria-controls={id}
				onClick={() => setOpen(!isOpen)}
			>
				<Ellipsis />
			</IconButton>
			{isOpen ? (
				<Stack
					id={id}
					position="absolute"
					right="0"
					top="full"
					mt="1"
					zIndex="30"
					minW="56"
					gap="0"
					p="1"
					rounded="card"
					borderWidth="1px"
					borderColor="border"
					bg="bg.panel"
					boxShadow="md"
				>
					<Button
						variant="ghost"
						size="sm"
						justifyContent="flex-start"
						onClick={() => {
							pin.mutate(!project.is_pinned);
							close();
						}}
					>
						{project.is_pinned ? <PinOff /> : <Pin />}
						{project.is_pinned ? "Unpin from Home" : "Pin to Home"}
					</Button>
					{project.role === "owner" ? (
						<Button
							variant="ghost"
							size="sm"
							justifyContent="flex-start"
							onClick={() => {
								setOpen(false);
								setMoving(true);
							}}
						>
							<FolderInput />
							Move to collection…
						</Button>
					) : null}
				</Stack>
			) : null}
			{isMoving ? (
				<MoveToCollection
					projectIds={[project.id]}
					current={project.collection_id}
					onClose={() => setMoving(false)}
				/>
			) : null}
		</Box>
	);
}

/** The collection a project is filed in, for its header; nothing when it is in none. */
export function CollectionBadge({
	collectionId,
}: {
	collectionId: string | null;
}) {
	const api = useApi();
	const collections = useQuery(collectionsQuery(api));
	const name = collections.data?.find((c) => c.id === collectionId)?.name;
	return name === undefined ? null : (
		<Badge variant="subtle" colorPalette="gray" flex="none" maxW="48" truncate>
			<FolderClosed />
			{name}
		</Badge>
	);
}
