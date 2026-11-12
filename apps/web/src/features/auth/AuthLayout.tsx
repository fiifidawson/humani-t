import { Box, Flex, Grid, Stack, Text } from "@chakra-ui/react";
import type { ReactNode } from "react";
import { ThemeButton } from "../../shell/ThemeButton";
import { LabMark, ProjectMark } from "../../ui/Brand";

/**
 * The cover beside the form, the one place LiGHT's light beam appears: the brand
 * keeps it for covers. Flat colour, never a fade; at 40% the text keeps 5.1:1.
 */
function Cover() {
	return (
		<Flex
			position="relative"
			overflow="hidden"
			direction="column"
			justify="space-between"
			gap="10"
			minH={{ base: "auto", lg: "100%" }}
			px={{ base: "6", md: "12" }}
			py={{ base: "8", md: "12" }}
			bg="light.darkBlue"
			color="light.eggshell"
		>
			<Box
				aria-hidden="true"
				position="absolute"
				inset="0"
				bg="light.lightBlue"
				opacity="0.4"
				clipPath="polygon(12% 0, 22% 0, 100% 45%, 100% 100%, 26% 100%)"
			/>
			<Flex position="relative" align="center" justify="space-between" gap="4">
				<ProjectMark size="xl" isOnColour />
				<LabMark isOnColour />
			</Flex>
			<Stack position="relative" gap="3" maxW="md">
				<Text
					fontFamily="heading"
					fontWeight="semibold"
					fontSize={{ base: "3xl", md: "5xl" }}
					lineHeight="1.15"
				>
					Evidence for humanitarian decisions
				</Text>
				<Text fontSize="lg">Searched, screened and cited.</Text>
			</Stack>
		</Flex>
	);
}

/** The cover and the form card that sign-in and enrolment share. */
export function AuthLayout({ children }: { children: ReactNode }) {
	return (
		<Grid
			minH="100%"
			templateColumns={{ base: "1fr", lg: "minmax(0, 5fr) minmax(0, 6fr)" }}
		>
			<Cover />
			<Flex direction="column" px="4" py={{ base: "6", md: "10" }} gap="6">
				<ThemeButton
					alignSelf="flex-end"
					display="inline-flex"
					alignItems="center"
					gap="2"
					px="4"
					py="2"
					rounded="pill"
					borderWidth="1px"
					borderColor="border.emphasized"
					bg="transparent"
					color="fg"
					fontSize="sm"
					cursor="pointer"
					_hover={{ bg: "bg.muted" }}
				/>
				<Flex flex="1" align="center" justify="center">
					<Box
						as="main"
						w="full"
						maxW="27rem"
						bg="bg.panel"
						borderWidth="1px"
						borderColor="border"
						rounded="card"
						p={{ base: "6", md: "8" }}
					>
						{children}
					</Box>
				</Flex>
			</Flex>
		</Grid>
	);
}
