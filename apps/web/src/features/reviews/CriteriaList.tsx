/**
 * Screening criteria as people read them: a labelled list, each item marked
 * with an icon for whether it lets a paper in or keeps it out. The criteria's
 * ids (I1, E1) stay in the data for provenance and are never shown.
 */

import { Box, chakra, Flex, Text } from "@chakra-ui/react";
import { CircleCheck, CircleX } from "lucide-react";
import { type ReactNode, useId } from "react";

export type CriterionMark = "meets" | "excludes" | "misses";

const MARKS: Readonly<Record<CriterionMark, [ReactNode, string]>> = {
	meets: [<CircleCheck key="m" size={16} />, "brand.fg"],
	excludes: [<CircleX key="e" size={16} />, "fg.muted"],
	misses: [<CircleX key="x" size={16} />, "fg.muted"],
};

export function CriteriaList({
	title,
	items,
}: {
	title: string;
	items: readonly { key: string; mark: CriterionMark; text: string }[];
}) {
	const id = useId();
	if (items.length === 0) {
		return null;
	}
	return (
		<Box>
			<Text id={id} fontWeight="medium">
				{title}
			</Text>
			{/* role="list": Safari drops list semantics when the bullets are removed. */}
			<chakra.ul
				role="list"
				aria-labelledby={id}
				listStyleType="none"
				p="0"
				mt="1.5"
				css={{ "& > li + li": { mt: "1.5" } }}
			>
				{items.map((item) => {
					const [icon, color] = MARKS[item.mark];
					return (
						<Flex as="li" key={item.key} gap="2" align="flex-start">
							<Box
								as="span"
								aria-hidden="true"
								color={color}
								flex="none"
								mt="1"
							>
								{icon}
							</Box>
							<span>
								{item.mark === "misses"
									? `Doesn't meet: ${item.text}`
									: item.text}
							</span>
						</Flex>
					);
				})}
			</chakra.ul>
		</Box>
	);
}
