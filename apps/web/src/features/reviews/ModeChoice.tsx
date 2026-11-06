/**
 * Quick or Deep (UI_UX_SPEC §6.3), as native radios: they need no script to
 * be keyboard-operable, and cost nothing in the bundle.
 */

import { Box, chakra, Flex, Stack, Text } from "@chakra-ui/react";
import { useId } from "react";
import { MODES, type PlanMode } from "./format";

interface ModeChoiceProps {
	value: PlanMode;
	onChange: (mode: PlanMode) => void;
}

export function ModeChoice({ value, onChange }: ModeChoiceProps) {
	const id = useId();
	return (
		<Box role="radiogroup" aria-labelledby={`${id}-label`}>
			<Text id={`${id}-label`} fontWeight="medium" fontSize="sm" mb="2">
				How thorough?
			</Text>
			<Stack gap="2">
				{MODES.map(({ mode, name, description }) => (
					<Flex key={mode} gap="2.5" align="flex-start">
						<chakra.input
							type="radio"
							id={`${id}-${mode}`}
							name={`${id}-mode`}
							value={mode}
							checked={value === mode}
							onChange={() => onChange(mode)}
							aria-describedby={`${id}-${mode}-hint`}
							mt="1"
							boxSize="4"
							accentColor="brand.solid"
							flex="none"
						/>
						<Box fontSize="sm">
							<chakra.label htmlFor={`${id}-${mode}`} fontWeight="medium">
								{name}
							</chakra.label>
							<Text id={`${id}-${mode}-hint`} color="fg.muted">
								{description}
							</Text>
						</Box>
					</Flex>
				))}
			</Stack>
		</Box>
	);
}
