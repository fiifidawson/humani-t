/**
 * Home's "Needs you" (UI_UX_SPEC §7.1): what waits for this person, one
 * action per row, and Dismiss. The API decides what is listed and for whom.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { unwrapEmpty } from "../../api/client";
import { useApi } from "../../app/api-context";
import { historyKeys, needsYouQuery } from "../../app/historyQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { SectionHeading } from "../../ui/SectionHeading";
import { useKeepProject } from "../retention/queries";
import { relativeTime } from "../reviews/format";
import { NeedsYouTarget } from "./ActivityLink";
import { NeedsYouIcon } from "./EventIcon";
import { type NeedsYouItem, needsYouAction, needsYouText } from "./words";

function Row({
	item,
	onDismiss,
	isDismissing,
	onKeep,
	isKeeping,
}: {
	item: NeedsYouItem;
	onDismiss: () => void;
	isDismissing: boolean;
	onKeep: () => void;
	isKeeping: boolean;
}) {
	const text = needsYouText(item);
	const action = needsYouAction(item);
	const about = `${item.project.name}: ${text}`;
	// A retention notice has no page to open: its one action is Keep, done here.
	const isRetention = item.kind === "retention";
	return (
		<Flex
			as="li"
			gap="3"
			align={{ base: "flex-start", md: "center" }}
			direction={{ base: "column", md: "row" }}
			py="3"
			borderBottomWidth="1px"
			borderColor="border"
			animationStyle="rise-in"
		>
			<Flex gap="2" flex="1" minW="0">
				<NeedsYouIcon item={item} />
				<Text minW="0" overflowWrap="anywhere">
					<Text as="span" fontWeight="semibold">
						{item.project.name}
					</Text>{" "}
					<Text as="span" color="fg.muted">
						{text}
						{isRetention ? null : (
							<>
								{" · "}
								<time dateTime={item.at}>{relativeTime(item.at)}</time>
							</>
						)}
					</Text>
				</Text>
			</Flex>
			<Flex gap="2" flex="none">
				{isRetention ? (
					<Button
						size="xs"
						aria-label={`${action}: ${about}`}
						disabled={isKeeping}
						onClick={onKeep}
					>
						{action}
					</Button>
				) : (
					<Button asChild size="xs">
						<NeedsYouTarget item={item} label={`${action}: ${about}`}>
							{action}
						</NeedsYouTarget>
					</Button>
				)}
				<Button
					size="xs"
					variant="ghost"
					aria-label={`Dismiss: ${about}`}
					disabled={isDismissing}
					onClick={onDismiss}
				>
					Dismiss
				</Button>
			</Flex>
		</Flex>
	);
}

export function NeedsYou() {
	const api = useApi();
	const queryClient = useQueryClient();
	const needs = useQuery(needsYouQuery(api));
	const dismiss = useMutation({
		mutationFn: (key: string) =>
			unwrapEmpty(api.POST("/api/v1/needs-you/dismissals", { body: { key } })),
		onSettled: () =>
			queryClient.invalidateQueries({ queryKey: historyKeys.needsYou }),
	});
	const keep = useKeepProject();

	if (needs.isError) {
		return (
			<section aria-labelledby="needs-you-heading">
				<SectionHeading id="needs-you-heading">Needs you</SectionHeading>
				<ErrorNotice error={needs.error} onRetry={() => void needs.refetch()} />
			</section>
		);
	}
	if (!needs.isSuccess || needs.data.count === 0) {
		return null;
	}
	return (
		<section aria-labelledby="needs-you-heading">
			<SectionHeading id="needs-you-heading">
				Needs you ({needs.data.count})
			</SectionHeading>
			{dismiss.isError ? <ErrorNotice error={dismiss.error} /> : null}
			{keep.isError ? <ErrorNotice error={keep.error} /> : null}
			<Stack as="ul" listStyle="none" m="0" p="0" gap="0">
				{needs.data.items.map((item) => (
					<Row
						key={item.key}
						item={item}
						isDismissing={dismiss.isPending && dismiss.variables === item.key}
						onDismiss={() => dismiss.mutate(item.key)}
						isKeeping={keep.isPending && keep.variables === item.project.id}
						onKeep={() => keep.mutate(item.project.id)}
					/>
				))}
			</Stack>
		</section>
	);
}
