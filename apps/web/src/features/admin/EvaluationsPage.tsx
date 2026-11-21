/**
 * Admin → Evaluations (UI_UX_SPEC §6.15): the evaluations of the projects this
 * administrator belongs to. The API decides who may see them; a refusal shows here.
 */

import {
	Button,
	Link as ChakraLink,
	Flex,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useApi } from "../../app/api-context";
import { evaluationListQuery } from "../../app/evaluationQueries";
import { projectListQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { PageHeading } from "../../ui/PageHeading";
import { fullDate, shortDate } from "../projects/format";
import { progressLine } from "./words";

export function EvaluationsPage() {
	const api = useApi();
	const evaluations = useQuery(evaluationListQuery(api));
	const projects = useQuery(projectListQuery(api, true));
	const nameOf = (projectId: string) =>
		projects.data?.find((p) => p.id === projectId)?.name ?? "A project";

	return (
		<Page>
			<Flex gap="4" align="center" justify="space-between" wrap="wrap">
				<PageHeading title="Evaluations" />
				{evaluations.isSuccess ? (
					<Button asChild>
						<Link to="/admin/evaluations/new">New evaluation</Link>
					</Button>
				) : null}
			</Flex>
			{evaluations.isPending ? <Loading what="the evaluations" /> : null}
			{evaluations.isError ? (
				<ErrorNotice
					error={evaluations.error}
					onRetry={() => void evaluations.refetch()}
				/>
			) : null}
			{evaluations.isSuccess && evaluations.data.length === 0 ? (
				<Text>
					No evaluations yet. Start one to compare humani-t's answers with
					another system's.
				</Text>
			) : null}
			{evaluations.isSuccess && evaluations.data.length > 0 ? (
				<Stack as="ul" aria-label="Evaluations" listStyle="none" m="0" p="0">
					{evaluations.data.map((evaluation) => (
						<Stack
							as="li"
							key={evaluation.id}
							gap="1"
							py="3"
							borderBottomWidth="1px"
							borderColor="border"
						>
							<ChakraLink asChild css={linkStyle} overflowWrap="anywhere">
								<Link
									to="/admin/evaluations/$evaluationId"
									params={{ evaluationId: evaluation.id }}
								>
									{nameOf(evaluation.project_id)}
								</Link>
							</ChakraLink>
							<Flex gap="3" wrap="wrap" color="fg.muted" fontSize="sm">
								<time
									dateTime={evaluation.created_at}
									title={fullDate(evaluation.created_at)}
								>
									{shortDate(evaluation.created_at)}
								</time>
								<Text as="span">
									{evaluation.question_count} questions ·{" "}
									{evaluation.system_count + 1} systems
								</Text>
								<Text as="span">{progressLine(evaluation)}</Text>
							</Flex>
						</Stack>
					))}
				</Stack>
			) : null}
		</Page>
	);
}
