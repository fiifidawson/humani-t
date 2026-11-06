import { Box, Flex, Heading, Stack } from "@chakra-ui/react";
import type { ReactNode } from "react";

/**
 * A section's h2 in LiGHT's pattern: a small black dot, a small all-caps label
 * at the top left, then a hairline rule under it.
 */
export function SectionHeading({
	id,
	children,
}: {
	id?: string;
	children: ReactNode;
}) {
	return (
		<Stack gap="2.5" mb="5">
			<Flex align="center" gap="2.5">
				<Box
					aria-hidden="true"
					boxSize="1.5"
					rounded="full"
					bg="marker"
					flex="none"
				/>
				<Heading
					as="h2"
					id={id}
					fontFamily="body"
					fontSize="xs"
					fontWeight="normal"
					lineHeight="1.4"
					textTransform="uppercase"
					letterSpacing="0.08em"
				>
					{children}
				</Heading>
			</Flex>
			<Box aria-hidden="true" borderTopWidth="1px" borderColor="border" />
		</Stack>
	);
}
