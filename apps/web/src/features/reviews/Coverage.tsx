/**
 * The coverage estimate (UI_UX_SPEC §6.8, ADR 0021): what share of the
 * relevant papers a run's searches found, as the API worked it out. Always a
 * range and always called an estimate; when the data can't support one, it
 * says so and why.
 */

import { Box, Text } from "@chakra-ui/react";
import { useId, useState } from "react";
import type { components } from "../../api/schema.gen";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { Reveal } from "../../ui/Reveal";
import { sourceName } from "./format";

type Coverage = components["schemas"]["CoverageOut"];
type Reason = NonNullable<Coverage["no_estimate_because"]>;

const NO_ESTIMATE: Readonly<Record<Reason, string>> = {
	screening_unfinished:
		"Screening hasn't finished, so not every paper has a label yet.",
	one_source:
		"Only one source was searched, so there is nothing to compare what it found with.",
	too_few_overlaps:
		"Too few of the relevant papers were found by more than one source to estimate what was missed.",
	sources_nested:
		"Every relevant paper one source found, another found too. That usually means they share records, not that nothing was missed.",
};

const METHODS: Readonly<Record<string, string>> = {
	"chapman-pairs": "capture–recapture between two sources (Chapman)",
};

function Meaning({
	coverage,
	interval,
}: {
	coverage: Coverage;
	interval: NonNullable<Coverage["interval"]>;
}) {
	const [isOpen, setOpen] = useState(false);
	const id = useId();
	const [first, second] = interval.sources;
	const pair = coverage.overlaps.find(
		(o) => o.sources[0] === first && o.sources[1] === second,
	);
	return (
		<Box mt="1">
			<DisclosureButton
				isOpen={isOpen}
				controls={id}
				onToggle={() => setOpen(!isOpen)}
			>
				What this estimate means
			</DisclosureButton>
			{isOpen ? (
				<Reveal>
					<Box id={id} mt="2" css={{ "& > * + *": { mt: "2" } }}>
						{pair === undefined ||
						first === undefined ||
						second === undefined ? null : (
							<Text>
								Of the {coverage.relevant_found} relevant papers this run found,{" "}
								{pair.both_found} were found by both {sourceName(first)} and{" "}
								{sourceName(second)}. The more relevant papers two sources
								share, the fewer they are likely to have both missed.
							</Text>
						)}
						<Text>
							Not a count of everything published: only what the sources index
							and the search matched.
						</Text>
						<Text>
							Overlapping sources make the top of the range likely too high.
						</Text>
						<Text>
							{coverage.rules_version === null
								? "It counts the labels as the model gave them."
								: `It counts the labels as the label rules (version ${coverage.rules_version}) set them.`}
						</Text>
						<Text color="fg.muted">
							Method: {METHODS[coverage.method] ?? coverage.method}, version{" "}
							{coverage.method_version}.
						</Text>
					</Box>
				</Reveal>
			) : null}
		</Box>
	);
}

export function CoverageEstimate({ coverage }: { coverage: Coverage }) {
	const id = useId();
	const { interval, no_estimate_because: reason } = coverage;
	return (
		<Box
			as="section"
			aria-labelledby={id}
			mb="5"
			p="4"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.subtle"
		>
			<Text id={id} fontSize="xs" fontWeight="semibold" color="fg.muted" mb="2">
				Coverage estimate
			</Text>
			{interval === null ? (
				<Text>
					No coverage estimate for this run.{" "}
					{reason === null ? "" : NO_ESTIMATE[reason]}
				</Text>
			) : (
				<>
					<Text>
						Estimate: this run found between {interval.low_percent}% and{" "}
						{interval.high_percent}% of the relevant papers its searches could
						reach.
					</Text>
					<Meaning coverage={coverage} interval={interval} />
				</>
			)}
		</Box>
	);
}
