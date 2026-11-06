import { Flex, type FlexProps } from "@chakra-ui/react";
import { COMPACT_ZOOM } from "../../shell/railStyles";

/** The chat column, drawn at 75% like the rail; the canvas beside it keeps its size. */
export function ChatZone(props: FlexProps) {
	return (
		<Flex
			// zoom, not a smaller font size: Chakra's spacing is in rem, which a
			// container's font size cannot reach, so only zoom shrinks the cards,
			// their spacing and the composer together.
			css={{ zoom: COMPACT_ZOOM }}
			{...props}
		/>
	);
}
