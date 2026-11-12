/** A menu of what a mask hides (`EntityType`), named in words. */

import { NativeSelect } from "@chakra-ui/react";
import { ENTITY_TYPES, type EntityType, entityTypeName } from "./masking";

export function EntityTypeSelect({
	id,
	value,
	onChange,
	size = "sm",
}: {
	id: string;
	value: EntityType;
	onChange: (type: EntityType) => void;
	size?: "sm" | "lg";
}) {
	return (
		<NativeSelect.Root size={size} width="auto">
			<NativeSelect.Field
				id={id}
				value={value}
				onChange={(event) => {
					const type = ENTITY_TYPES.find((t) => t === event.target.value);
					if (type !== undefined) {
						onChange(type);
					}
				}}
			>
				{ENTITY_TYPES.map((type) => (
					<option key={type} value={type}>
						{entityTypeName(type)}
					</option>
				))}
			</NativeSelect.Field>
			<NativeSelect.Indicator />
		</NativeSelect.Root>
	);
}
