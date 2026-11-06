import { Skeleton, VisuallyHidden } from "@chakra-ui/react";

/** A skeleton block in the shape of what is coming (UI_UX_SPEC §6), named for screen readers. */
export function Loading({ what }: { what: string }) {
	return (
		<Skeleton role="status" height="24" rounded="card">
			<VisuallyHidden>Loading {what}…</VisuallyHidden>
		</Skeleton>
	);
}
