/**
 * The source panel (UI_UX_SPEC §6.10): where a citation comes from, without
 * leaving the brief. The passages are the exact masked text the Writer saw;
 * "Cited for" lists every claim citing the tag, so each can be checked
 * against them. A tag with no passage is the checks' "source missing".
 *
 * It slides over the canvas's right edge (the whole width on a phone). It
 * takes focus when it opens, and Esc closes it before the canvas.
 */

import {
	Box,
	Button,
	Link as ChakraLink,
	chakra,
	Flex,
	Heading,
	IconButton,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { evidenceQuery } from "../../app/briefQueries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { LinkButton, linkStyle } from "../../ui/LinkButton";
import { Loading } from "../../ui/Loading";
import { MaskedText } from "../reviews/MaskedText";
import type { BriefSource } from "./Claim";
import { SECTIONS, screeningWord } from "./words";

type Evidence = components["schemas"]["EvidenceOut"];

function Passages({ evidence }: { evidence: Evidence }) {
	const [index, setIndex] = useState(0);
	const passage = evidence.passages[index];
	const total = evidence.passages.length;
	if (passage === undefined) {
		return <Text color="fg.muted">No passage was kept for this source.</Text>;
	}
	return (
		<Box>
			<Text color="fg.muted" fontSize="xs" mb="1.5" role="status">
				Passage {index + 1} of {total} · found by: {passage.found_by}
			</Text>
			<Box
				as="blockquote"
				m="0"
				pl="3"
				borderLeftWidth="2px"
				borderColor="border.emphasized"
			>
				<MaskedText text={passage.text} />
			</Box>
			{total > 1 ? (
				<Flex gap="2" mt="2" justify="flex-end">
					<Button
						size="xs"
						variant="outline"
						disabled={index === 0}
						onClick={() => setIndex(index - 1)}
					>
						Previous passage
					</Button>
					<Button
						size="xs"
						variant="outline"
						disabled={index === total - 1}
						onClick={() => setIndex(index + 1)}
					>
						Next passage
					</Button>
				</Flex>
			) : null}
		</Box>
	);
}

function Found({ evidence }: { evidence: Evidence }) {
	const [isWhyOpen, setWhyOpen] = useState(false);
	const id = useId();
	const details = evidence.source;
	const label = screeningWord(details?.screening_label ?? null);
	return (
		<Stack gap="4">
			{details === null ? (
				<Text>
					{evidence.source_kind === "document"
						? "One of your project's documents."
						: "Nothing more was known about this source when the brief was written."}
				</Text>
			) : (
				<Box>
					<Text fontWeight="semibold">{details.title}</Text>
					<Text color="fg.muted">
						{[
							details.year,
							details.doi === null ? null : `DOI ${details.doi}`,
							label,
						]
							.filter((part) => part !== null)
							.join(" · ")}
					</Text>
					{details.url === null ? null : (
						<ChakraLink
							href={details.url}
							target="_blank"
							rel="noopener noreferrer"
							css={linkStyle}
						>
							Open the source
						</ChakraLink>
					)}
				</Box>
			)}
			{evidence.cited_for.length === 0 ? null : (
				<Box>
					<Text id={`${id}-cited`} fontWeight="medium" mb="1">
						Cited for
					</Text>
					<chakra.ul aria-labelledby={`${id}-cited`} pl="5">
						{evidence.cited_for.map((claim) => (
							<li key={claim.path}>
								“{claim.text}”{" "}
								<Text as="span" color="fg.muted">
									(
									{[
										claim.option,
										claim.criterion,
										SECTIONS[claim.section] ?? claim.section,
									]
										.filter((part) => part !== null)
										.join(", ")}
									)
								</Text>
							</li>
						))}
					</chakra.ul>
				</Box>
			)}
			<Passages evidence={evidence} />
			{details === null ? null : (
				<Box>
					<LinkButton
						type="button"
						fontSize="sm"
						aria-expanded={isWhyOpen}
						aria-controls={`${id}-why`}
						onClick={() => setWhyOpen(!isWhyOpen)}
					>
						Why it was included
					</LinkButton>
					{isWhyOpen ? (
						<Text id={`${id}-why`} mt="1.5">
							{details.rationale ??
								"No screening reason was recorded for this source."}
						</Text>
					) : null}
				</Box>
			)}
		</Stack>
	);
}

export function SourcePanel({
	briefId,
	tag,
	source,
	onClose,
}: {
	briefId: string;
	tag: string;
	source: BriefSource | undefined;
	onClose: () => void;
}) {
	const api = useApi();
	const evidence = useQuery(evidenceQuery(api, briefId, tag));
	const heading = useRef<HTMLHeadingElement>(null);
	const id = useId();

	useEffect(() => {
		heading.current?.focus({ preventScroll: true });
	}, []);

	useEffect(() => {
		// Capture phase: the canvas listens to Esc too, and this panel goes first.
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === "Escape" && !event.defaultPrevented) {
				event.preventDefault();
				onClose();
			}
		};
		document.addEventListener("keydown", onKeyDown, true);
		return () => document.removeEventListener("keydown", onKeyDown, true);
	}, [onClose]);

	return (
		<Box
			as="aside"
			aria-labelledby={id}
			position="absolute"
			top="0"
			right="0"
			bottom="0"
			w={{ base: "full", md: "400px" }}
			zIndex="2"
			display="flex"
			flexDirection="column"
			bg="bg.panel"
			borderLeftWidth="1px"
			borderColor="border"
			boxShadow="lg"
			fontSize="sm"
		>
			<Flex
				align="center"
				gap="2"
				px="4"
				py="3"
				borderBottomWidth="1px"
				borderColor="border"
			>
				<Heading
					as="h3"
					id={id}
					ref={heading}
					tabIndex={-1}
					flex="1"
					fontSize="md"
					fontWeight="semibold"
				>
					{tag}
				</Heading>
				<IconButton
					variant="ghost"
					rounded="card"
					size="sm"
					aria-label="Close source"
					onClick={onClose}
				>
					<X />
				</IconButton>
			</Flex>
			<Box flex="1" overflowY="auto" px="4" py="4">
				{isApiError(evidence.error, "not-found") ? (
					<Stack gap="2">
						{source?.title == null ? null : (
							<Text fontWeight="semibold">{source.title}</Text>
						)}
						<Text
							color="caveat.fg"
							display="flex"
							gap="1.5"
							alignItems="center"
						>
							<TriangleAlert size={16} />
							This citation has no passage. Report it.
						</Text>
					</Stack>
				) : evidence.isError ? (
					<ErrorNotice
						error={evidence.error}
						onRetry={() => void evidence.refetch()}
					/>
				) : evidence.data === undefined ? (
					<Loading what="the source" />
				) : (
					<Found evidence={evidence.data} />
				)}
			</Box>
		</Box>
	);
}
