/**
 * The brief card (UI_UX_SPEC §5.3.8), posted once the Brief stage finishes:
 * the bottom line whole, the options with their strength and no ranking, the
 * counts or the failing checks, and the short caveat, always.
 */

import { Button, chakra, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { FileText, Route, TriangleAlert } from "lucide-react";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { briefQuery } from "../../app/briefQueries";

import { ChatCard } from "../reviews/ChatCard";
import { plural } from "../reviews/format";
import type { LiveRun } from "../reviews/liveRun";
import { ErrorCard } from "../reviews/PlanCards";
import { useReview } from "../reviews/reviewContext";
import { Claims } from "./Claim";
import { STRENGTHS, stoppedShort } from "./words";

export function BriefCard({ live }: { live: LiveRun }) {
	const api = useApi();
	const navigate = useNavigate();
	const { project, review } = useReview();
	const isWritten = live.state.summaries.answer !== undefined;
	const brief = useQuery({
		...briefQuery(api, review.id, live.run.id),
		enabled: isWritten,
	});
	const params = { projectId: project.id, reviewId: review.id };

	if (!isWritten || brief.isPending || isApiError(brief.error, "not-found")) {
		return null;
	}
	if (brief.isError) {
		return (
			<ErrorCard
				title="The decision brief couldn't be loaded"
				error={brief.error}
				onRetry={() => void brief.refetch()}
			/>
		);
	}
	const data = brief.data;
	const openSource = (tag: string) =>
		void navigate({
			to: "/p/$projectId/r/$reviewId/brief",
			params,
			search: { source: tag },
		});
	const warnings = data.warnings.length;

	return (
		<ChatCard
			title={`Decision brief · round ${data.kept_round} of ${data.max_rounds} · ${stoppedShort(data.stopped_because)}`}
			author="Brief writer"
			at={data.created_at}
			actions={
				<>
					<Button asChild size="sm" variant="outline">
						<Link to="/p/$projectId/r/$reviewId/brief" params={params}>
							<FileText />
							Open brief
						</Link>
					</Button>
					<Button asChild size="sm" variant="outline">
						<Link to="/p/$projectId/r/$reviewId/trace" params={params}>
							<Route />
							How this was made
						</Link>
					</Button>
				</>
			}
		>
			<Claims
				claims={data.bottom_line}
				sources={data.sources}
				onOpen={openSource}
			/>
			<chakra.ul
				listStyleType="none"
				p="0"
				display="flex"
				flexWrap="wrap"
				columnGap="4"
			>
				{data.options.map((option) => (
					<li key={option.name}>
						{`${option.name} · ${STRENGTHS[option.strength].word}`}
					</li>
				))}
			</chakra.ul>
			{warnings === 0 ? (
				<Text color="fg.muted">
					{[
						plural(data.conflicts.length, "disagreement"),
						plural(data.gaps.length, "gap"),
						plural(data.sources.length, "source"),
					].join(" · ")}
				</Text>
			) : (
				<Text color="caveat.fg" display="flex" gap="1.5" alignItems="center">
					<TriangleAlert size={16} />
					{`${plural(warnings, "check")} still failing · see the brief`}
				</Text>
			)}
			<Text color="caveat.fg" display="flex" gap="1.5" alignItems="center">
				<TriangleAlert size={16} />
				An aid to a decision, not the decision. Check the sources.
			</Text>
		</ChatCard>
	);
}
