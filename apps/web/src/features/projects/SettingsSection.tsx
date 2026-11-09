/** One titled, bordered block of the project settings page (UI_UX_SPEC §6.14). */

import { Box, Heading } from "@chakra-ui/react";
import { type ReactNode, useId } from "react";

export function SettingsSection({
	title,
	isDanger = false,
	children,
}: {
	title: string;
	isDanger?: boolean;
	children: ReactNode;
}) {
	const headingId = useId();
	return (
		<Box
			as="section"
			aria-labelledby={headingId}
			rounded="card"
			borderWidth="1px"
			borderColor={isDanger ? "border.error" : "border"}
			px="5"
			py="4"
		>
			<Heading
				id={headingId}
				as="h2"
				fontSize="lg"
				fontWeight="semibold"
				mb="3"
			>
				{title}
			</Heading>
			{children}
		</Box>
	);
}
