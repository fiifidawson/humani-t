/**
 * Project settings, Privacy (UI_UX_SPEC §6.14): the terms always masked in this
 * project's texts. Owners only, for reading too, because a term is usually a
 * name; the API audits every listing, so the list is fetched once per visit.
 */

import { Box, Button, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { unwrapEmpty } from "../../api/client";
import { isApiError, userMessage } from "../../api/problem";
import { useApi } from "../../app/api-context";
import {
	addMaskingRule,
	type MaskingRule,
	maskingRulesQuery,
} from "../../app/documentQueries";
import { keys } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Loading } from "../../ui/Loading";
import { EntityTypeSelect } from "../reviews/EntityTypeSelect";
import { type EntityType, entityTypeName } from "../reviews/masking";

export function MaskingRulesSection({
	projectId,
	isOwner,
}: {
	projectId: string;
	isOwner: boolean;
}) {
	if (!isOwner) {
		return (
			<Text fontSize="sm">
				Only owners can see and change the masking rules, because they hold
				names.
			</Text>
		);
	}
	return <OwnerRules projectId={projectId} />;
}

function OwnerRules({ projectId }: { projectId: string }) {
	const api = useApi();
	const queryClient = useQueryClient();
	const rules = useQuery(maskingRulesQuery(api, projectId));
	const key = keys.maskingRules(projectId);
	// Kept in the cache by hand: fetching the list again would audit it again.
	const update = (change: (current: MaskingRule[]) => MaskingRule[]) =>
		queryClient.setQueryData<MaskingRule[]>(key, (current) =>
			change(current ?? []),
		);

	const remove = useMutation({
		mutationFn: (rule: MaskingRule) =>
			unwrapEmpty(
				api.DELETE("/api/v1/projects/{project_id}/masking-rules/{rule_id}", {
					params: { path: { project_id: projectId, rule_id: rule.id } },
				}),
			),
		onSuccess: (_, rule) =>
			update((current) => current.filter((r) => r.id !== rule.id)),
		// Another owner removed it first: what the person asked for is done.
		onError: (error, rule) => {
			if (isApiError(error, "not-found")) {
				update((current) => current.filter((r) => r.id !== rule.id));
			}
		},
	});

	return (
		<Stack gap="4" fontSize="sm">
			<Text>
				Terms masked in every text of this project before any model reads it,
				such as staff names or a clinic's name. Matching ignores case and needs
				whole words. Removing a rule doesn't unmask texts already masked.
			</Text>
			{rules.isPending ? <Loading what="the masking rules" /> : null}
			{rules.isError ? (
				<ErrorNotice error={rules.error} onRetry={() => void rules.refetch()} />
			) : null}
			{rules.data === undefined ? null : rules.data.length === 0 ? (
				<Text>No masking rules yet.</Text>
			) : (
				<Stack as="ul" aria-label="Masking rules" gap="1" listStyle="none">
					{rules.data.map((rule) => (
						<Flex
							as="li"
							key={rule.id}
							gap="3"
							align="center"
							justify="space-between"
							py="1"
							borderBottomWidth="1px"
							borderColor="border"
						>
							<Text overflowWrap="anywhere">
								{`${rule.term} · ${entityTypeName(rule.entity_type)}`}
							</Text>
							<Button
								size="xs"
								variant="outline"
								aria-label={`Remove ${rule.term}`}
								disabled={remove.isPending}
								onClick={() => remove.mutate(rule)}
							>
								<Trash2 />
								Remove
							</Button>
						</Flex>
					))}
				</Stack>
			)}
			{remove.isError && !isApiError(remove.error, "not-found") ? (
				<ErrorNotice error={remove.error} />
			) : null}
			<AddRuleForm
				projectId={projectId}
				onAdded={(rule) => update((current) => [...current, rule])}
			/>
		</Stack>
	);
}

function AddRuleForm({
	projectId,
	onAdded,
}: {
	projectId: string;
	onAdded: (rule: MaskingRule) => void;
}) {
	const api = useApi();
	const typeId = useId();
	const [term, setTerm] = useState("");
	const [type, setType] = useState<EntityType>("PERSON");
	const add = useMutation({
		mutationFn: () =>
			addMaskingRule(api, projectId, { term, entity_type: type }),
		onSuccess: (rule) => {
			setTerm("");
			onAdded(rule);
		},
	});
	// A refused term (too short, already a rule) is about the field; the rest is not.
	const termError = isApiError(add.error, "invalid", "conflict")
		? userMessage(add.error)
		: undefined;
	const submit = (event: FormEvent) => {
		event.preventDefault();
		if (!add.isPending) {
			add.mutate();
		}
	};

	return (
		<Stack as="form" gap="3" onSubmit={submit}>
			{add.isError && termError === undefined ? (
				<ErrorNotice error={add.error} />
			) : null}
			<Flex gap="3" wrap="wrap" align="flex-start">
				<Box flex="1" minW="56">
					<Field
						label="Term"
						hint="A name, place or code to mask everywhere in this project."
						maxLength={200}
						required
						autoComplete="off"
						value={term}
						error={termError}
						onChange={(event) => setTerm(event.target.value)}
					/>
				</Box>
				<Stack gap="1.5">
					<chakra.label htmlFor={typeId} fontSize="sm" fontWeight="medium">
						Mask as
					</chakra.label>
					<EntityTypeSelect
						id={typeId}
						size="lg"
						value={type}
						onChange={setType}
					/>
				</Stack>
			</Flex>
			<Button
				type="submit"
				size="sm"
				alignSelf="flex-start"
				disabled={add.isPending}
			>
				<Plus />
				Add rule
			</Button>
		</Stack>
	);
}
