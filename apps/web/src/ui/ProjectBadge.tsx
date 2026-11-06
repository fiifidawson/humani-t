import { Flex } from "@chakra-ui/react";
import { initials } from "./initials";

/**
 * A project's initials on a soft brand tint: how a project is told apart where its
 * name is hidden, as in the folded rail. A rounded square, where the person's own
 * avatar is round. Decorative: the name always sits beside it, visibly or for
 * screen readers.
 */
export function ProjectBadge({ name }: { name: string }) {
	return (
		<Flex
			aria-hidden="true"
			data-project-badge=""
			boxSize="7"
			flex="none"
			rounded="md"
			align="center"
			justify="center"
			bg="brand.muted"
			color="brand.fg"
			fontSize="xs"
			fontWeight="semibold"
			lineHeight="1"
		>
			{initials(name)}
		</Flex>
	);
}
