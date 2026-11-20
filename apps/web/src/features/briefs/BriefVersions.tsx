/**
 * The brief's versions (UI_UX_SPEC §6.9 Versions): a menu to open another
 * version, and what changed against one chosen to compare with. The API
 * computes every change; this screen only words it.
 */

import { Box, chakra, Flex, NativeSelect, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import {
	briefComparisonQuery,
	briefVersionsQuery,
} from "../../app/comparisonQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Reveal } from "../../ui/Reveal";
import { clockTime } from "../reviews/format";
import { useReview } from "../reviews/reviewContext";
import { STRENGTHS, type Strength } from "./words";

type Schemas = components["schemas"];
type Version = Schemas["BriefVersionOut"];
type Changes = Schemas["BriefChangesOut"];

const versionName = (v: Version) =>
	v.is_latest
		? `Version ${v.version} (latest)`
		: `Version ${v.version}, ${clockTime(v.created_at)}`;

/** "Weak", as in "Cash: Weak → Moderate". */
const strengthWord = (strength: Strength) =>
	STRENGTHS[strength].word.replace(/ evidence$/, "");

function Line({ name, children }: { name: string; children: string }) {
	return (
		<li>
			<Text as="span" fontWeight="medium">
				{`${name}:`}
			</Text>{" "}
			{children}
		</li>
	);
}

/** What changed between two versions or two runs' briefs, as the API compared them. */
export function BriefChanges({ changes }: { changes: Changes }) {
	const sources = (list: Changes["sources_added"]) =>
		list.map((s) => `${s.tag} ${s.title ?? "Untitled source"}`).join("; ");
	const isSame =
		changes.options_added.length +
			changes.options_removed.length +
			changes.strength_changes.length +
			changes.sources_added.length +
			changes.sources_removed.length ===
		0;
	if (isSame) {
		return <Text>Same options, evidence strengths and sources.</Text>;
	}
	return (
		<chakra.ul pl="5">
			{changes.options_added.length === 0 ? null : (
				<Line name="Options added">{changes.options_added.join(", ")}</Line>
			)}
			{changes.options_removed.length === 0 ? null : (
				<Line name="Options removed">{changes.options_removed.join(", ")}</Line>
			)}
			{changes.strength_changes.length === 0 ? null : (
				<Line name="Evidence strength">
					{changes.strength_changes
						.map(
							(c) =>
								`${c.option}: ${strengthWord(c.before)} → ${strengthWord(c.after)}`,
						)
						.join("; ")}
				</Line>
			)}
			{changes.sources_added.length === 0 ? null : (
				<Line name="Sources added">{sources(changes.sources_added)}</Line>
			)}
			{changes.sources_removed.length === 0 ? null : (
				<Line name="Sources removed">{sources(changes.sources_removed)}</Line>
			)}
		</chakra.ul>
	);
}

function Comparison({
	briefId,
	otherId,
}: {
	briefId: string;
	otherId: string;
}) {
	const api = useApi();
	const id = useId();
	const compared = useQuery(briefComparisonQuery(api, briefId, otherId));
	if (compared.isError) {
		return (
			<ErrorNotice
				error={compared.error}
				onRetry={() => void compared.refetch()}
			/>
		);
	}
	if (compared.data === undefined) {
		return <Loading what="the comparison" />;
	}
	const { before } = compared.data;
	return (
		<Box
			as="section"
			aria-labelledby={id}
			p="3"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
		>
			<Text id={id} fontWeight="semibold" mb="1">
				{`Changes since version ${before.version}`}
			</Text>
			<BriefChanges changes={compared.data} />
		</Box>
	);
}

/** Shown once a review has a second version; keyed by brief, so a new one starts fresh. */
export function BriefVersions({ brief }: { brief: Schemas["BriefOut"] }) {
	const api = useApi();
	const { project, review } = useReview();
	const navigate = useNavigate();
	const id = useId();
	const [otherId, setOtherId] = useState("");
	const versions = useQuery(briefVersionsQuery(api, review.id)).data ?? [];
	if (versions.length < 2) {
		return null;
	}
	const open = (briefId: string) => {
		const chosen = versions.find((v) => v.id === briefId);
		if (chosen !== undefined) {
			void navigate({
				to: "/p/$projectId/r/$reviewId/brief",
				params: { projectId: project.id, reviewId: review.id },
				search: { run: chosen.is_latest ? undefined : chosen.run_id },
			});
		}
	};
	return (
		<Box>
			<Flex gap="4" wrap="wrap" align="center">
				<Flex gap="2" align="center">
					<chakra.label htmlFor={`${id}-version`}>Version</chakra.label>
					<NativeSelect.Root size="sm" width="auto">
						<NativeSelect.Field
							id={`${id}-version`}
							value={brief.id}
							onChange={(event) => open(event.target.value)}
						>
							{versions.map((v) => (
								<option key={v.id} value={v.id}>
									{versionName(v)}
								</option>
							))}
						</NativeSelect.Field>
						<NativeSelect.Indicator />
					</NativeSelect.Root>
				</Flex>
				<Flex gap="2" align="center">
					<chakra.label htmlFor={`${id}-compare`}>Compare with</chakra.label>
					<NativeSelect.Root size="sm" width="auto">
						<NativeSelect.Field
							id={`${id}-compare`}
							value={otherId}
							onChange={(event) => setOtherId(event.target.value)}
						>
							<option value="">No other version</option>
							{versions
								.filter((v) => v.id !== brief.id)
								.map((v) => (
									<option key={v.id} value={v.id}>
										{`Version ${v.version}`}
									</option>
								))}
						</NativeSelect.Field>
						<NativeSelect.Indicator />
					</NativeSelect.Root>
				</Flex>
			</Flex>
			{otherId === "" ? null : (
				<Reveal>
					<Box mt="3">
						<Comparison briefId={brief.id} otherId={otherId} />
					</Box>
				</Reveal>
			)}
		</Box>
	);
}
