/**
 * Compare runs (UI_UX_SPEC §7.4): two runs of a review, part by part, read
 * from the older to the newer. The API computes every difference from what
 * each run recorded; this canvas only lays them out.
 */

import { Box, chakra, Table, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { type ReactNode, useId } from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { runComparisonQuery } from "../../app/comparisonQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { BriefChanges } from "../briefs/BriefVersions";
import { screeningWord } from "../briefs/words";
import { RunComparePicker } from "../runs/RunComparePicker";
import { Canvas, CanvasHeading } from "./Canvas";
import { MaskedText } from "./MaskedText";
import { useReview } from "./reviewContext";

type Schemas = components["schemas"];
type Comparison = Schemas["RunComparisonOut"];

const FROM = "/signed-in/p/$projectId/r/$reviewId/compare" as const;

const SETTING_NAMES: Readonly<Record<string, string>> = {
	criteria_version: "Criteria version",
	plan_version: "Plan version",
	instructions_version: "Instructions version",
	masking: "Masking",
};

const LABELS = ["include", "exclude", "uncertain", "unscreened"] as const;

/** A recorded value in words: lists joined, nothing as "none". */
function shown(value: unknown): string {
	if (value === null || value === undefined) {
		return "none";
	}
	if (Array.isArray(value)) {
		return value.length === 0 ? "none" : value.map(shown).join(", ");
	}
	if (typeof value === "boolean") {
		return value ? "yes" : "no";
	}
	return typeof value === "object" ? JSON.stringify(value) : String(value);
}

const labelWord = (label: string) =>
	label === "unscreened" ? "Not screened" : (screeningWord(label) ?? label);

function Part({ title, children }: { title: string; children: ReactNode }) {
	const id = useId();
	return (
		<Box as="section" aria-labelledby={id}>
			<CanvasHeading id={id}>{title}</CanvasHeading>
			{children}
		</Box>
	);
}

function SideBySide({
	compared,
	caption,
	rows,
}: {
	compared: Comparison;
	caption: string;
	rows: readonly [string, string, string][];
}) {
	return (
		<Table.Root size="sm" width="auto">
			<Table.Caption srOnly>{caption}</Table.Caption>
			<Table.Header>
				<Table.Row>
					<Table.ColumnHeader scope="col">
						<chakra.span srOnly>What</chakra.span>
					</Table.ColumnHeader>
					<Table.ColumnHeader scope="col">{`Run ${compared.before.number}`}</Table.ColumnHeader>
					<Table.ColumnHeader scope="col">{`Run ${compared.after.number}`}</Table.ColumnHeader>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{rows.map(([name, before, after]) => (
					<Table.Row key={name}>
						<Table.Cell as="th" scope="row" fontWeight="medium">
							{name}
						</Table.Cell>
						<Table.Cell>{before}</Table.Cell>
						<Table.Cell>{after}</Table.Cell>
					</Table.Row>
				))}
			</Table.Body>
		</Table.Root>
	);
}

function Line({ name, children }: { name: string; children: ReactNode }) {
	return (
		<li>
			<Text as="span" fontWeight="medium">
				{`${name}:`}
			</Text>{" "}
			{children}
		</li>
	);
}

function Setup({
	compared,
	setup,
}: {
	compared: Comparison;
	setup: Schemas["SetupOut"];
}) {
	const queries = (list: Schemas["SourceQueryOut"][]) =>
		list.length === 0
			? "none"
			: list.map((q) => `${q.source}: ${q.query}`).join("; ");
	return (
		<Part title="Setup">
			<SideBySide
				compared={compared}
				caption="What each run was set up with"
				rows={[
					[
						"Vocabulary version",
						shown(setup.vocabulary_versions.before),
						shown(setup.vocabulary_versions.after),
					],
					["Sources", shown(setup.sources.before), shown(setup.sources.after)],
					[
						"Model",
						shown(setup.model_ids.before),
						shown(setup.model_ids.after),
					],
					...setup.prompt_versions.map((p): [string, string, string] => [
						`Prompt: ${p.name}`,
						shown(p.before),
						shown(p.after),
					]),
				]}
			/>
			<chakra.ul pl="5" mt="3">
				<Line name="Queries added">{queries(setup.queries_added)}</Line>
				<Line name="Queries removed">{queries(setup.queries_removed)}</Line>
				<Line name="Settings that changed">
					{setup.settings_changed.length === 0
						? "none"
						: setup.settings_changed
								.map(
									(c) =>
										`${SETTING_NAMES[c.name] ?? c.name}: ${shown(c.before)} → ${shown(c.after)}`,
								)
								.join("; ")}
				</Line>
			</chakra.ul>
		</Part>
	);
}

function PaperList({
	name,
	papers,
}: {
	name: string;
	papers: Schemas["ComparedPaperOut"][];
}) {
	return (
		<Box mt="3">
			<Text fontWeight="medium">{`${name} (${papers.length})`}</Text>
			{papers.length === 0 ? null : (
				<chakra.ul pl="5">
					{papers.map((paper) => (
						<li key={paper.paper_id}>
							<MaskedText text={paper.title} isInline />
						</li>
					))}
				</chakra.ul>
			)}
		</Box>
	);
}

function Discovery({
	compared,
	discovery,
}: {
	compared: Comparison;
	discovery: Schemas["DiscoveryOut"];
}) {
	return (
		<Part title="Discovery">
			<SideBySide
				compared={compared}
				caption="Papers each run found"
				rows={[
					[
						"Papers found",
						String(discovery.before.identified),
						String(discovery.after.identified),
					],
					[
						"After duplicates",
						String(discovery.before.after_deduplication),
						String(discovery.after.after_deduplication),
					],
				]}
			/>
			<PaperList
				name={`Only in run ${compared.before.number}`}
				papers={discovery.only_before}
			/>
			<PaperList
				name={`Only in run ${compared.after.number}`}
				papers={discovery.only_after}
			/>
			<Text mt="3">{`In both: ${discovery.in_both}`}</Text>
		</Part>
	);
}

function Screening({
	compared,
	screening,
}: {
	compared: Comparison;
	screening: Schemas["ScreeningOut"];
}) {
	return (
		<Part title="Screening">
			<SideBySide
				compared={compared}
				caption="Papers per label in each run"
				rows={LABELS.map((label) => [
					labelWord(label),
					String(screening.before[label] ?? 0),
					String(screening.after[label] ?? 0),
				])}
			/>
			<Text fontWeight="medium" mt="3">
				{`Labels that changed (${screening.changed.length})`}
			</Text>
			{screening.changed.length === 0 ? null : (
				<chakra.ul pl="5">
					{screening.changed.map((change) => (
						<li key={change.paper_id}>
							<MaskedText text={change.title} isInline />
							{`: ${labelWord(change.before)} → ${labelWord(change.after)}.`}
							{change.reason === null ? null : (
								<>
									{" "}
									<MaskedText text={change.reason} isInline />
								</>
							)}
						</li>
					))}
				</chakra.ul>
			)}
		</Part>
	);
}

function Comparing({ runId, otherId }: { runId: string; otherId: string }) {
	const api = useApi();
	const compared = useQuery(runComparisonQuery(api, runId, otherId));
	if (compared.isError) {
		return (
			<ErrorNotice
				error={compared.error}
				onRetry={() => void compared.refetch()}
			/>
		);
	}
	const data = compared.data;
	if (data === undefined) {
		return <Loading what="the comparison" />;
	}
	if (data.cannot_compare !== null) {
		return <Text mt="4">{data.cannot_compare}</Text>;
	}
	return (
		<>
			{data.setup === null ? null : (
				<Setup compared={data} setup={data.setup} />
			)}
			{data.discovery === null ? null : (
				<Discovery compared={data} discovery={data.discovery} />
			)}
			{data.screening === null ? null : (
				<Screening compared={data} screening={data.screening} />
			)}
			<Part title="Brief">
				{data.brief === null ? (
					<Text>
						One of these runs wrote no brief, so there is none to compare.
					</Text>
				) : (
					<BriefChanges changes={data.brief} />
				)}
			</Part>
		</>
	);
}

export function CompareRunsCanvas() {
	const { project, review } = useReview();
	const { a, b } = useSearch({ from: FROM });
	return (
		<Canvas title="Compare runs">
			<RunComparePicker
				key={`${a ?? ""}-${b ?? ""}`}
				projectId={project.id}
				reviewId={review.id}
				runId={a}
				otherRunId={b}
			/>
			{a === undefined || b === undefined ? (
				<Text mt="4">Choose two runs to compare.</Text>
			) : (
				<Comparing runId={a} otherId={b} />
			)}
		</Canvas>
	);
}
