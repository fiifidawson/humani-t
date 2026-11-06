import { Flex } from "@chakra-ui/react";
import type { ReactNode } from "react";

interface FormErrorProps {
	icon?: ReactNode;
	/** "alert" when it appears in answer to something the person did. */
	role?: "alert";
	children: ReactNode;
}

/** A form's own error line: an icon and words, never colour alone (UI_UX_SPEC §9.2). */
export function FormError({ icon, role, children }: FormErrorProps) {
	return (
		<Flex
			as="p"
			gap="2"
			align="flex-start"
			px="3"
			py="2.5"
			rounded="card"
			bg="danger.subtle"
			color="fg"
			fontSize="sm"
			css={{ "& > svg": { flex: "none", mt: "0.5", color: "fg.error" } }}
			role={role}
		>
			{icon}
			<span>{children}</span>
		</Flex>
	);
}
