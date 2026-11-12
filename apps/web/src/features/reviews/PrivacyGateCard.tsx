/**
 * The privacy review gate in the chat (UI_UX_SPEC §5.3.6): the documents waiting
 * for an owner's masking review, and for owners a link to each one's review on
 * the documents screen. Deciding happens there; this card only points to it.
 */

import { Link as ChakraLink, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useApi } from "../../app/api-context";
import { documentsQuery } from "../../app/documentQueries";
import type { Summary } from "../runs/runEvents";
import { ChatCard } from "./ChatCard";
import { totalMasked } from "./masking";

interface Waiting {
	documentId: string;
	maskedItems: Record<string, number>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === "object" && value !== null && !Array.isArray(value);

/** The gate's payload is an open object in the API's schema, so it is read with care. */
function waitingDocuments(payload: Summary | null): Waiting[] {
	const documents = payload?.["documents"];
	if (!Array.isArray(documents)) {
		return [];
	}
	return documents.flatMap((document: unknown) => {
		if (!isRecord(document) || typeof document["document_id"] !== "string") {
			return [];
		}
		const items = isRecord(document["masked_items"])
			? document["masked_items"]
			: {};
		return [
			{
				documentId: document["document_id"],
				maskedItems: Object.fromEntries(
					Object.entries(items).filter(
						(entry): entry is [string, number] => typeof entry[1] === "number",
					),
				),
			},
		];
	});
}

export function PrivacyGateCard({
	projectId,
	isOwner,
	payload,
}: {
	projectId: string;
	isOwner: boolean;
	payload: Summary | null;
}) {
	const api = useApi();
	const waiting = waitingDocuments(payload);
	// The payload holds ids and counts only; the names come from the listing.
	const listing = useQuery({
		...documentsQuery(api, projectId, false),
		enabled: waiting.length > 0,
	});

	return (
		<ChatCard title="Needs you · Privacy review" tone="warning">
			<Text>
				Uploaded documents need a masking check before any model reads them. The
				run continues when an owner approves.
			</Text>
			{waiting.length === 0 ? null : (
				<Stack as="ul" gap="1" listStyle="none">
					{waiting.map(({ documentId, maskedItems }) => {
						const document = listing.data?.documents.find(
							(d) => d.id === documentId,
						);
						const label =
							document === undefined
								? "A document"
								: `${document.tag} ${document.name}`;
						const total = totalMasked(maskedItems);
						return (
							<Flex
								as="li"
								key={documentId}
								gap="3"
								justify="space-between"
								wrap="wrap"
							>
								<Text overflowWrap="anywhere">
									{`${label} · ${total === 0 ? "nothing masked" : `${total} masked`}`}
								</Text>
								{isOwner ? (
									<ChakraLink asChild>
										<Link
											to="/p/$projectId/documents"
											params={{ projectId }}
											search={{ document: documentId }}
											aria-label={`Review masking ${label}`}
										>
											Review masking
										</Link>
									</ChakraLink>
								) : null}
							</Flex>
						);
					})}
				</Stack>
			)}
			{isOwner ? null : <Text color="fg.muted">Only owners can do this.</Text>}
		</ChatCard>
	);
}
