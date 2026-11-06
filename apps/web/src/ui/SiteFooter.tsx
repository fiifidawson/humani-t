import { Flex, Stack, Text } from "@chakra-ui/react";
import { LabMark, ProjectMark } from "./Brand";

/** The footer, after LiGHT's site: a hairline, the project on the left, the lab on the right. */
export function SiteFooter() {
	return (
		<Flex
			as="footer"
			borderTopWidth="1px"
			borderColor="border"
			px={{ base: "4", md: "8" }}
			py="6"
			gap="4"
			align="center"
			justify="space-between"
			wrap="wrap"
		>
			<Stack gap="2">
				<ProjectMark size="md" />
				<Text fontSize="sm" color="fg.muted">
					Evidence for humanitarian decisions.
				</Text>
			</Stack>
			<LabMark />
		</Flex>
	);
}
