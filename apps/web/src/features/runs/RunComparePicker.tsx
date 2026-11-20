/**
 * "Compare runs: [Run 3 ▾] with [Run 1 ▾] [Compare]" (UI_UX_SPEC §7.4). Needs
 * only the project and review ids, so any page can host it: the run canvas
 * today, the project timeline too. Hidden until the review has two runs.
 */

import { Button, chakra, Flex, NativeSelect, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { useApi } from "../../app/api-context";
import { runsQuery } from "../../app/reviewQueries";

interface RunOption {
	id: string;
	name: string;
}

function RunSelect({
	id,
	label,
	value,
	runs,
	onChange,
}: {
	id?: string;
	label?: string;
	value: string;
	runs: readonly RunOption[];
	onChange: (runId: string) => void;
}) {
	return (
		<NativeSelect.Root size="sm" width="auto">
			<NativeSelect.Field
				id={id}
				aria-label={label}
				value={value}
				onChange={(event) => onChange(event.target.value)}
			>
				{runs.map((run) => (
					<option key={run.id} value={run.id}>
						{run.name}
					</option>
				))}
			</NativeSelect.Field>
			<NativeSelect.Indicator />
		</NativeSelect.Root>
	);
}

export function RunComparePicker({
	projectId,
	reviewId,
	runId,
	otherRunId,
}: {
	projectId: string;
	reviewId: string;
	/** Chosen first; the newest run if left out. */
	runId?: string | undefined;
	/** Chosen second; the run before the first if left out. */
	otherRunId?: string | undefined;
}) {
	const api = useApi();
	const navigate = useNavigate();
	const id = useId();
	const newestFirst = useQuery(runsQuery(api, reviewId)).data ?? [];
	const runs = newestFirst.map((run, index) => ({
		id: run.id,
		name: `Run ${newestFirst.length - index}`,
	}));
	const at = Math.max(
		0,
		runs.findIndex((run) => run.id === runId),
	);
	const [chosen, setChosen] = useState<[string, string] | null>(null);
	if (runs.length < 2) {
		return null;
	}
	const [first, second] = chosen ?? [
		runs[at]?.id ?? "",
		otherRunId ?? runs[at + 1]?.id ?? runs[0]?.id ?? "",
	];
	return (
		<Flex role="group" aria-labelledby={id} gap="2" align="center" wrap="wrap">
			<Text>
				<span id={id}>Compare runs</span>:
			</Text>
			<RunSelect
				label="Run"
				value={first}
				runs={runs}
				onChange={(runId) => setChosen([runId, second])}
			/>
			<chakra.label htmlFor={`${id}-with`}>with</chakra.label>
			<RunSelect
				id={`${id}-with`}
				value={second}
				runs={runs}
				onChange={(runId) => setChosen([first, runId])}
			/>
			<Button
				size="sm"
				variant="outline"
				disabled={first === second}
				onClick={() =>
					void navigate({
						to: "/p/$projectId/r/$reviewId/compare",
						params: { projectId, reviewId },
						search: { a: first, b: second },
					})
				}
			>
				Compare
			</Button>
		</Flex>
	);
}
