import { Stack } from "@chakra-ui/react";
import type { ReactNode } from "react";

/** A scrolling page's column: generous space, and a narrower measure for forms. */
export function Page({
	children,
	isNarrow = false,
}: {
	children: ReactNode;
	isNarrow?: boolean;
}) {
	return (
		<Stack
			gap="10"
			w="full"
			maxW={isNarrow ? "2xl" : "6xl"}
			mx="auto"
			px={{ base: "4", md: "8" }}
			pt={{ base: "8", md: "12" }}
			pb="16"
			css={{ "& > *": { minW: "0" } }}
		>
			{children}
		</Stack>
	);
}
