/**
 * The All projects page's filters as chips and menus (UI_UX_SPEC §7.2). Every
 * choice is written to the URL, so a filtered view can be shared or bookmarked.
 *
 * The menus offer what the person's own projects hold: their tags and people.
 */

import {
	Button,
	Checkbox,
	chakra,
	Flex,
	IconButton,
	NativeSelect,
	Stack,
	Text,
} from "@chakra-ui/react";
import { X } from "lucide-react";
import { useId } from "react";
import type { ProjectSummary } from "./format";
import { STATUS_LABELS } from "./ProjectStatusBadge";
import { type ProjectsSearch, STATUS_FILTERS } from "./projectListView";

interface ProjectFiltersProps {
	search: ProjectsSearch;
	onChange: (changes: Partial<ProjectsSearch>) => void;
	/** Every project the person can see, archived ones too: where the choices come from. */
	everything: readonly ProjectSummary[];
	collectionName: string | undefined;
}

type Options = readonly (readonly [value: string, label: string])[];

/** A labelled native select: keyboard and screen-reader friendly with no script. */
export function LabelledSelect({
	label,
	value,
	options,
	onChange,
}: {
	label: string;
	value: string;
	options: Options;
	onChange: (value: string) => void;
}) {
	const id = useId();
	return (
		<Flex align="center" gap="2">
			<chakra.label htmlFor={id} fontSize="sm" color="fg.muted">
				{label}
			</chakra.label>
			<NativeSelect.Root size="sm" width="auto">
				<NativeSelect.Field
					id={id}
					value={value}
					onChange={(event) => onChange(event.target.value)}
				>
					{options.map(([choice, text]) => (
						<option key={choice} value={choice}>
							{text}
						</option>
					))}
				</NativeSelect.Field>
				<NativeSelect.Indicator />
			</NativeSelect.Root>
		</Flex>
	);
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
	return (
		<Flex
			as="li"
			align="center"
			gap="1"
			pl="2.5"
			pr="0.5"
			rounded="full"
			borderWidth="1px"
			borderColor="border.emphasized"
			fontSize="sm"
		>
			{label}
			<IconButton
				variant="ghost"
				size="2xs"
				rounded="full"
				aria-label={`Remove filter: ${label}`}
				onClick={onRemove}
			>
				<X />
			</IconButton>
		</Flex>
	);
}

function people(
	projects: readonly ProjectSummary[],
	isOwnerOnly: boolean,
): Options {
	const named = new Map<string, string>();
	for (const project of projects) {
		for (const member of project.members) {
			if (!isOwnerOnly || member.role === "owner") {
				named.set(member.user_id, member.display_name);
			}
		}
	}
	return [...named].sort(([, a], [, b]) => a.localeCompare(b));
}

export function ProjectFilters({
	search,
	onChange,
	everything,
	collectionName,
}: ProjectFiltersProps) {
	const chosenTags = search.tag ?? [];
	const tagChoices = [
		...new Map(
			everything.flatMap((p) => p.tags).map((t) => [t.toLocaleLowerCase(), t]),
		).values(),
	]
		.filter(
			(t) =>
				!chosenTags.some(
					(c) => c.toLocaleLowerCase() === t.toLocaleLowerCase(),
				),
		)
		.sort((a, b) => a.localeCompare(b));
	const fromId = useId();
	const toId = useId();
	const isFiltered = [
		search.status,
		search.tag,
		search.owner,
		search.member,
		search.from,
		search.to,
		search.collection,
		search.archived,
	].some((value) => value !== undefined);

	return (
		<Stack gap="3" as="fieldset">
			<chakra.legend srOnly>Filters</chakra.legend>
			<Flex gap="4" wrap="wrap" align="center">
				<LabelledSelect
					label="Status"
					value={search.status?.[0] ?? ""}
					onChange={(value: string) =>
						onChange({
							status: STATUS_FILTERS.filter((s) => s === value),
						})
					}
					options={[
						["", "Any"],
						...STATUS_FILTERS.map((s) => [s, STATUS_LABELS[s]] as const),
					]}
				/>
				<LabelledSelect
					label="Owner"
					value={search.owner ?? ""}
					onChange={(value: string) =>
						onChange({ owner: value === "" ? undefined : value })
					}
					options={[["", "Anyone"], ...people(everything, true)]}
				/>
				<LabelledSelect
					label="Member"
					value={search.member ?? ""}
					onChange={(value: string) =>
						onChange({ member: value === "" ? undefined : value })
					}
					options={[["", "Anyone"], ...people(everything, false)]}
				/>
				{tagChoices.length === 0 ? null : (
					<LabelledSelect
						label="Add a tag"
						value={""}
						onChange={(value: string) =>
							onChange({ tag: [...chosenTags, value] })
						}
						options={[
							["", "Choose…"],
							...tagChoices.map((t) => [t, t] as const),
						]}
					/>
				)}
			</Flex>
			<Flex gap="4" wrap="wrap" align="center">
				<Flex align="center" gap="2">
					<chakra.label htmlFor={fromId} fontSize="sm" color="fg.muted">
						Created from
					</chakra.label>
					<chakra.input
						id={fromId}
						type="date"
						value={search.from ?? ""}
						onChange={(event) =>
							onChange({ from: event.target.value || undefined })
						}
						fontSize="sm"
						px="2"
						py="1"
						rounded="card"
						borderWidth="1px"
						borderColor="border"
						bg="bg"
					/>
					<chakra.label htmlFor={toId} fontSize="sm" color="fg.muted">
						to
					</chakra.label>
					<chakra.input
						id={toId}
						type="date"
						value={search.to ?? ""}
						onChange={(event) =>
							onChange({ to: event.target.value || undefined })
						}
						fontSize="sm"
						px="2"
						py="1"
						rounded="card"
						borderWidth="1px"
						borderColor="border"
						bg="bg"
					/>
				</Flex>
				<Checkbox.Root
					checked={search.archived === true}
					onCheckedChange={(details) =>
						onChange({ archived: details.checked === true ? true : undefined })
					}
				>
					<Checkbox.HiddenInput />
					<Checkbox.Control />
					<Checkbox.Label>Show archived projects</Checkbox.Label>
				</Checkbox.Root>
			</Flex>
			{isFiltered ? (
				<Flex gap="2" wrap="wrap" align="center">
					<Text fontSize="sm" color="fg.muted">
						Showing:
					</Text>
					<Flex as="ul" listStyle="none" m="0" p="0" gap="2" wrap="wrap">
						{(search.status ?? []).map((status) => (
							<Chip
								key={status}
								label={STATUS_LABELS[status]}
								onRemove={() =>
									onChange({
										status: (search.status ?? []).filter((s) => s !== status),
									})
								}
							/>
						))}
						{chosenTags.map((tag) => (
							<Chip
								key={tag}
								label={`Tag: ${tag}`}
								onRemove={() =>
									onChange({ tag: chosenTags.filter((t) => t !== tag) })
								}
							/>
						))}
						{search.collection === undefined ? null : (
							<Chip
								label={
									search.collection === "none"
										? "Not in a collection"
										: `Collection: ${collectionName ?? "…"}`
								}
								onRemove={() => onChange({ collection: undefined })}
							/>
						)}
					</Flex>
					<Button
						size="xs"
						variant="ghost"
						onClick={() =>
							onChange({
								status: undefined,
								tag: undefined,
								owner: undefined,
								member: undefined,
								from: undefined,
								to: undefined,
								collection: undefined,
								archived: undefined,
							})
						}
					>
						Clear filters
					</Button>
				</Flex>
			) : null}
		</Stack>
	);
}
