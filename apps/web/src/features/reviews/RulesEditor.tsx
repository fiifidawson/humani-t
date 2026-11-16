/**
 * The label rules (UI_UX_SPEC §6.8): a side form on the papers canvas that
 * previews how many papers new rules would move, then applies them, and the
 * grey chat note each applied version leaves (§5.3.11).
 *
 * What a rule does to a paper is the API's: this sends the two choices and
 * shows the counts it answers with. Applying is confirmed, never optimistic (§11.3).
 */

import {
	Box,
	Button,
	chakra,
	Flex,
	RadioGroup,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useId, useState } from "react";
import { unwrap } from "../../api/client";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { keys } from "../../app/queries";
import { labelRulesQuery } from "../../app/reviewQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { plural, TIERS } from "./format";
import { useReview } from "./reviewContext";

type Schemas = components["schemas"];
type Rules = Schemas["LabelRulesIn"];
type Unclear = Rules["unclear_counts_as"];
type Preview = Schemas["RulesPreviewOut"];

const UNCLEAR_CHOICES: readonly { value: Unclear; label: string }[] = [
	{ value: "met", label: "Met" },
	{ value: "not_met", label: "Not met" },
	{ value: "uncertain", label: "Uncertain (a person decides)" },
];

const DEFAULTS: Rules = { possibly_min_met: 1, unclear_counts_as: "not_met" };

/** One grey line in the chat for each applied version of the rules. */
export function useRulesNotes(): { at: string; node: ReactNode }[] {
	const api = useApi();
	const { review } = useReview();
	const rules = useQuery(labelRulesQuery(api, review.id));
	return (rules.data?.versions ?? []).map((version) => ({
		at: version.changed_at,
		node: (
			<Text
				key={`rules-${version.version}`}
				px="1"
				fontSize="sm"
				color="fg.muted"
			>
				Labels changed by new rules: {plural(version.papers_moved, "paper")}{" "}
				moved
			</Text>
		),
	}));
}

function PreviewLines({ preview }: { preview: Preview }) {
	return (
		<Box role="status">
			<Box as="ul" listStyleType="none" p="0" m="0">
				{preview.moves.length === 0 ? <li>No papers would move.</li> : null}
				{preview.moves.map((move) => (
					<li key={`${move.from_tier}-${move.to_tier}`}>
						{move.count} would move from {TIERS[move.from_tier].word} to{" "}
						{TIERS[move.to_tier].word}
					</li>
				))}
				{preview.untouchable > 0 ? (
					<Text as="li" color="fg.muted">
						{plural(preview.untouchable, "paper")} can't be relabelled (no
						per-criterion answers)
					</Text>
				) : null}
			</Box>
		</Box>
	);
}

function RulesForm({
	id,
	initial,
	onApplied,
}: {
	id: string;
	initial: Rules;
	onApplied: (moved: number) => void;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const { review } = useReview();
	const [count, setCount] = useState(String(initial.possibly_min_met));
	const [unclear, setUnclear] = useState<Unclear>(initial.unclear_counts_as);
	const legendId = useId();
	const path = { path: { review_id: review.id } };
	const rules = (): Rules => ({
		possibly_min_met: Number(count),
		unclear_counts_as: unclear,
	});

	const preview = useMutation({
		mutationFn: (body: Rules) =>
			unwrap(
				api.POST("/api/v1/reviews/{review_id}/label-rules/preview", {
					params: path,
					body,
				}),
			),
	});
	const apply = useMutation({
		mutationFn: (body: Rules) =>
			unwrap(
				api.PUT("/api/v1/reviews/{review_id}/label-rules", {
					params: path,
					body,
				}),
			),
		onSuccess: async (applied) => {
			await Promise.all([
				queryClient.invalidateQueries({ queryKey: keys.labelRules(review.id) }),
				queryClient.invalidateQueries({
					queryKey: ["reviews", review.id, "papers"],
				}),
				queryClient.invalidateQueries({
					queryKey: ["reviews", review.id, "prisma"],
				}),
			]);
			onApplied(applied.moved);
		},
	});
	// A preview speaks for the rules it was asked about; changing one asks again.
	const changed = () => {
		preview.reset();
		apply.reset();
	};

	return (
		<chakra.form
			id={id}
			aria-label="Label rules"
			noValidate
			mt="3"
			p="4"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.subtle"
			onSubmit={(event) => {
				event.preventDefault();
				preview.mutate(rules());
			}}
		>
			<Stack gap="4">
				<Text color="fg.muted">
					Labels are worked out again from each paper's answers to the criteria.
					The model isn't asked again.
				</Text>
				<Field
					label="Possibly relevant needs at least this many inclusion criteria met"
					type="number"
					min={1}
					step={1}
					value={count}
					onChange={(event) => {
						setCount(event.target.value);
						changed();
					}}
				/>
				<RadioGroup.Root
					value={unclear}
					onValueChange={(details) => {
						const choice = UNCLEAR_CHOICES.find(
							(option) => option.value === details.value,
						);
						if (choice !== undefined) {
							setUnclear(choice.value);
							changed();
						}
					}}
					aria-labelledby={legendId}
				>
					<Text id={legendId} fontSize="sm" fontWeight="medium" mb="2">
						An unclear answer to an inclusion criterion counts as
					</Text>
					<Flex gap="4" wrap="wrap">
						{UNCLEAR_CHOICES.map((option) => (
							<RadioGroup.Item key={option.value} value={option.value}>
								<RadioGroup.ItemHiddenInput />
								<RadioGroup.ItemIndicator />
								<RadioGroup.ItemText>{option.label}</RadioGroup.ItemText>
							</RadioGroup.Item>
						))}
					</Flex>
				</RadioGroup.Root>
				{preview.isError ? <ErrorNotice error={preview.error} /> : null}
				{apply.isError ? <ErrorNotice error={apply.error} /> : null}
				{preview.data === undefined ? null : (
					<PreviewLines preview={preview.data} />
				)}
				<Flex gap="2" wrap="wrap">
					<Button
						type="submit"
						size="sm"
						variant="outline"
						loading={preview.isPending}
						loadingText="Previewing…"
					>
						Preview
					</Button>
					{preview.data === undefined ? null : (
						<Button
							type="button"
							size="sm"
							disabled={apply.isPending}
							onClick={() => apply.mutate(rules())}
						>
							{apply.isPending ? "Applying…" : "Apply"}
						</Button>
					)}
				</Flex>
			</Stack>
		</chakra.form>
	);
}

/** "Edit rules" and its side form, for the members who may change labels. */
export function RulesEditor() {
	const api = useApi();
	const { review, canManage } = useReview();
	const [isOpen, setOpen] = useState(false);
	const [applied, setApplied] = useState<number | null>(null);
	const id = useId();
	const current = useQuery({
		...labelRulesQuery(api, review.id),
		enabled: canManage,
	});
	if (!canManage) {
		return null;
	}
	const inForce = current.data?.current;
	return (
		<Box mt="5">
			<Button
				size="xs"
				variant="outline"
				aria-expanded={isOpen}
				aria-controls={isOpen ? id : undefined}
				onClick={() => {
					setOpen(!isOpen);
					setApplied(null);
				}}
			>
				Edit rules
			</Button>
			{applied === null ? null : (
				<Text mt="2" role="status">
					Rules applied: {plural(applied, "paper")} moved.
				</Text>
			)}
			{isOpen ? (
				current.isError ? (
					<ErrorNotice error={current.error} />
				) : current.data === undefined ? null : (
					<RulesForm
						id={id}
						initial={
							inForce == null
								? DEFAULTS
								: {
										possibly_min_met: inForce.possibly_min_met,
										unclear_counts_as: inForce.unclear_counts_as,
									}
						}
						onApplied={(moved) => {
							setOpen(false);
							setApplied(moved);
						}}
					/>
				)
			) : null}
		</Box>
	);
}
