/**
 * One evaluation (UI_UX_SPEC §6.15): a results table per system pair, then each
 * judgement as the judge saw it, A and B side by side, the systems hidden until
 * revealed so the reader is not swayed either. Polled while it is still running.
 */

import {
	Box,
	Button,
	chakra,
	Grid,
	Heading,
	Stack,
	Table,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useId, useState } from "react";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { evaluationQuery } from "../../app/evaluationQueries";
import { projectListQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { SectionHeading } from "../../ui/SectionHeading";
import { SCORES, scoreWord } from "../briefs/words";
import { modelLine, progressLine } from "./words";

type Evaluation = components["schemas"]["EvaluationOut"];
type PairResult = components["schemas"]["PairResultOut"];
type Judgement = components["schemas"]["JudgementOut"];
type Scores = components["schemas"]["QualityOut"];

const METRIC_LABELS: ReadonlyMap<string, string> = new Map(SCORES);

const signed = (value: number): string =>
	`${value > 0 ? "+" : ""}${value.toFixed(2)}`;

function PairTable({ pair }: { pair: PairResult }) {
	return (
		<Stack gap="2">
			<Box overflowX="auto">
				<Table.Root size="sm">
					<Table.Caption
						captionSide="top"
						textAlign="start"
						fontWeight="medium"
					>
						{pair.first} compared with {pair.second}
					</Table.Caption>
					<Table.Header>
						<Table.Row>
							<Table.ColumnHeader>Check</Table.ColumnHeader>
							<Table.ColumnHeader textAlign="end">
								{pair.first}
							</Table.ColumnHeader>
							<Table.ColumnHeader textAlign="end">
								{pair.second}
							</Table.ColumnHeader>
							<Table.ColumnHeader textAlign="end">
								Difference
							</Table.ColumnHeader>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{pair.metrics.map((m) => (
							<Table.Row key={m.metric}>
								<Table.Cell>
									{METRIC_LABELS.get(m.metric) ?? m.metric}
								</Table.Cell>
								<Table.Cell textAlign="end" fontVariantNumeric="tabular-nums">
									{m.first_mean.toFixed(2)}
								</Table.Cell>
								<Table.Cell textAlign="end" fontVariantNumeric="tabular-nums">
									{m.second_mean.toFixed(2)}
								</Table.Cell>
								<Table.Cell textAlign="end" fontVariantNumeric="tabular-nums">
									{signed(m.difference)}
								</Table.Cell>
							</Table.Row>
						))}
					</Table.Body>
				</Table.Root>
			</Box>
			<Text fontSize="sm" color="fg.muted">
				Means of the judge's scores, 0 (poor) to 2 (good).
			</Text>
			<Text>
				{pair.first} won {pair.wins}, tied {pair.ties} and lost {pair.losses} of{" "}
				{pair.judged} questions.
			</Text>
		</Stack>
	);
}

function AnswerPanel({
	letter,
	system,
	answer,
	scores,
	isRevealed,
}: {
	letter: "A" | "B";
	system: string;
	answer: string;
	scores: Scores;
	isRevealed: boolean;
}) {
	const headingId = useId();
	return (
		<Stack
			as="section"
			aria-labelledby={headingId}
			gap="3"
			p="4"
			borderWidth="1px"
			borderColor="border"
			rounded="card"
			minW="0"
		>
			<Heading as="h4" id={headingId} fontSize="md" fontWeight="semibold">
				Answer {letter}
				{isRevealed ? (
					<chakra.span fontWeight="normal">: {system}</chakra.span>
				) : null}
			</Heading>
			<Text whiteSpace="pre-wrap" overflowWrap="anywhere">
				{answer}
			</Text>
			<chakra.ul listStyle="none" m="0" p="0" fontSize="sm" color="fg.muted">
				{SCORES.map(([key, label]) => (
					<li key={key}>
						{label}: {scores[key]} of 2, {scoreWord(scores[key])}
					</li>
				))}
			</chakra.ul>
		</Stack>
	);
}

const PREFERRED: Readonly<Record<Judgement["winner"], string>> = {
	a: "The judge preferred A.",
	b: "The judge preferred B.",
	tie: "The judge called it a tie.",
};

function JudgementCard({ judgement }: { judgement: Judgement }) {
	const [isRevealed, setRevealed] = useState(false);
	const headingId = useId();
	return (
		<Stack
			as="article"
			aria-labelledby={headingId}
			gap="4"
			py="6"
			borderBottomWidth="1px"
			borderColor="border"
		>
			<Heading as="h3" id={headingId} fontSize="lg" fontWeight="semibold">
				Question {judgement.position}
			</Heading>
			<Text overflowWrap="anywhere">{judgement.question}</Text>
			<Grid templateColumns={{ base: "1fr", md: "1fr 1fr" }} gap="4">
				<AnswerPanel
					letter="A"
					system={judgement.system_a}
					answer={judgement.answer_a}
					scores={judgement.scores_a}
					isRevealed={isRevealed}
				/>
				<AnswerPanel
					letter="B"
					system={judgement.system_b}
					answer={judgement.answer_b}
					scores={judgement.scores_b}
					isRevealed={isRevealed}
				/>
			</Grid>
			<Text fontWeight="medium">{PREFERRED[judgement.winner]}</Text>
			<Text>{judgement.reason}</Text>
			<Button
				variant="outline"
				size="sm"
				alignSelf="flex-start"
				aria-pressed={isRevealed}
				onClick={() => setRevealed((shown) => !shown)}
			>
				Reveal systems
			</Button>
		</Stack>
	);
}

function AnswersOnly({ evaluation }: { evaluation: Evaluation }) {
	return (
		<Stack gap="4">
			<Text>
				No other system's answers were uploaded, so nothing was judged.
			</Text>
			<Stack as="ol" listStyle="none" m="0" p="0" gap="4">
				{evaluation.answers.map((a) => (
					<Stack as="li" key={a.position} gap="1">
						<Text fontWeight="medium" overflowWrap="anywhere">
							{a.position}. {a.question}
						</Text>
						<Text whiteSpace="pre-wrap" overflowWrap="anywhere">
							{a.answer}
						</Text>
					</Stack>
				))}
			</Stack>
		</Stack>
	);
}

export function EvaluationPage() {
	const api = useApi();
	const { evaluationId } = useParams({
		from: "/signed-in/admin/evaluations/$evaluationId",
	});
	const evaluation = useQuery(evaluationQuery(api, evaluationId));
	const projects = useQuery(projectListQuery(api, true));
	const projectName = projects.data?.find(
		(p) => p.id === evaluation.data?.project_id,
	)?.name;
	const title =
		projectName === undefined ? "Evaluation" : `Evaluation of ${projectName}`;

	return (
		<Page>
			<PageHeading title={title} />
			{evaluation.isPending ? <Loading what="the evaluation" /> : null}
			{evaluation.isError ? (
				<ErrorNotice
					error={evaluation.error}
					onRetry={() => void evaluation.refetch()}
				/>
			) : null}
			{evaluation.isSuccess ? <Evaluated evaluation={evaluation.data} /> : null}
		</Page>
	);
}

function Evaluated({ evaluation }: { evaluation: Evaluation }) {
	const hasOthers = evaluation.systems.length > 1;
	return (
		<Stack gap="10">
			<Stack gap="1">
				<Text fontWeight="medium">{progressLine(evaluation)}</Text>
				<Text color="fg.muted" fontSize="sm">
					{evaluation.question_count} questions ·{" "}
					{evaluation.systems.join(", ")}
					{" · "}
					{evaluation.is_masked
						? "Masked before it was stored or sent."
						: "Masking was off for this project: stored and sent as written."}
				</Text>
			</Stack>
			{hasOthers ? (
				<chakra.section aria-labelledby="evaluation-results">
					<SectionHeading id="evaluation-results">Results</SectionHeading>
					{evaluation.results.length === 0 ? (
						<Text>Nothing has been judged yet.</Text>
					) : (
						<Stack gap="8">
							{evaluation.results.map((pair) => (
								<PairTable key={pair.second} pair={pair} />
							))}
						</Stack>
					)}
				</chakra.section>
			) : null}
			<chakra.section aria-labelledby="evaluation-judgements">
				<SectionHeading id="evaluation-judgements">
					{hasOthers ? "Judgements" : "humani-t's answers"}
				</SectionHeading>
				{hasOthers ? (
					evaluation.judgements.map((j) => (
						<JudgementCard
							key={`${j.position}-${j.system_a}-${j.system_b}`}
							judgement={j}
						/>
					))
				) : (
					<AnswersOnly evaluation={evaluation} />
				)}
			</chakra.section>
			<Text as="footer" fontSize="sm" color="fg.muted">
				{modelLine(evaluation)}
			</Text>
		</Stack>
	);
}
