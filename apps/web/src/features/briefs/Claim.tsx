/**
 * How a brief's statements are shown (UI_UX_SPEC §8.1, §8.2, §8.4): the
 * strength meter with its word, a claim with its citation chips, or marked as
 * reasoning with none. These are the pieces every brief view shares.
 */

import { Box, chakra, Text } from "@chakra-ui/react";
import { Circle, FileText, Folder, Sparkle, TriangleAlert } from "lucide-react";
import type { components } from "../../api/schema.gen";
import { MaskedText } from "../reviews/MaskedText";
import { STRENGTHS, type Strength } from "./words";

type Schemas = components["schemas"];
export type Claim = Schemas["ClaimOut"];
export type BriefSource = Schemas["app__api__schemas__briefs__SourceOut"];
/** What a chip needs of a source: a brief's sources and an answer's both give it. */
export type ChipSource = Pick<
	BriefSource,
	"tag" | "has_passage" | "source_kind" | "title" | "year"
>;

/** The word is the label; the meter repeats it and is hidden from screen readers (§10.2). */
export function EvidenceBadge({ strength }: { strength: Strength }) {
	const { word, dots, tone, meaning } = STRENGTHS[strength];
	return (
		<Box
			as="span"
			display="inline-flex"
			alignItems="center"
			gap="1.5"
			title={meaning}
		>
			<Box as="span" display="inline-flex" gap="0.5" color={tone}>
				{[0, 1, 2].map((dot) => (
					<Circle
						key={dot}
						size={9}
						fill={dot < dots ? "currentColor" : "none"}
					/>
				))}
			</Box>
			<Text as="span" fontWeight="medium" fontSize="sm">
				{word}
			</Text>
		</Box>
	);
}

const Chip = chakra("button", {
	base: {
		display: "inline-flex",
		alignItems: "center",
		gap: "1",
		minH: "6",
		px: "1.5",
		mx: "0.5",
		rounded: "card",
		borderWidth: "1px",
		borderColor: "border.emphasized",
		bg: "bg.panel",
		color: "link",
		fontSize: "xs",
		fontWeight: "medium",
		fontStyle: "normal",
		verticalAlign: "baseline",
		cursor: "pointer",
		transition: "background-color 0.2s ease",
		_hover: { bg: "bg.muted" },
		"& > svg": { flex: "none" },
	},
});

/** A source tag that opens the source panel (§8.4). A tag with no passage says so. */
export function CitationChip({
	tag,
	source,
	onOpen,
}: {
	tag: string;
	source: ChipSource | undefined;
	onOpen: (tag: string) => void;
}) {
	const isMissing = source === undefined || !source.has_passage;
	const isDocument = source?.source_kind === "document" || tag.startsWith("D");
	const details = isMissing
		? "source missing"
		: [source.title, source.year].filter((part) => part != null).join(", ");
	return (
		<Chip
			type="button"
			aria-label={`Source ${tag}${details === "" ? "" : `, ${details}`}`}
			onClick={() => onOpen(tag)}
			color={isMissing ? "caveat.fg" : "link"}
		>
			{isMissing ? (
				<TriangleAlert size={12} />
			) : isDocument ? (
				<Folder size={12} />
			) : (
				<FileText size={12} />
			)}
			{tag}
			{isMissing ? " source missing" : null}
		</Chip>
	);
}

/**
 * One claim (§8.2): evidence in plain text with its chips, or the model's
 * reasoning in italics, marked, and never with a chip.
 */
export function ClaimText({
	claim,
	sources,
	onOpen,
}: {
	claim: Claim;
	sources: readonly ChipSource[];
	onOpen: (tag: string) => void;
}) {
	if (claim.support === "hypothesis") {
		return (
			<chakra.span fontStyle="italic">
				<MaskedText text={claim.text} isInline />{" "}
				<Text
					as="span"
					display="inline-flex"
					alignItems="center"
					gap="1"
					fontSize="xs"
					color="fg.muted"
					fontStyle="normal"
				>
					<Sparkle size={12} />
					reasoning, not a source
				</Text>
			</chakra.span>
		);
	}
	return (
		<span>
			<MaskedText text={claim.text} isInline />
			{claim.citations.map((citation) => (
				<CitationChip
					key={citation.tag}
					tag={citation.tag}
					source={sources.find((s) => s.tag === citation.tag)}
					onOpen={onOpen}
				/>
			))}
		</span>
	);
}

/** Several claims as one paragraph, in order: the bottom line. */
export function Claims({
	claims,
	sources,
	onOpen,
}: {
	claims: readonly Claim[];
	sources: readonly ChipSource[];
	onOpen: (tag: string) => void;
}) {
	return (
		<Text>
			{claims.map((claim, index) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: claims keep the Writer's order and never move
				<span key={index}>
					{index === 0 ? null : " "}
					<ClaimText claim={claim} sources={sources} onOpen={onOpen} />
				</span>
			))}
		</Text>
	);
}
