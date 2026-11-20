/** Pin or unpin a project for yourself (UI_UX_SPEC §7.1). Pins are per person. */

import { IconButton } from "@chakra-ui/react";
import { Pin, PinOff } from "lucide-react";
import { usePin } from "./organise";

export function PinButton({
	projectId,
	name,
	isPinned,
}: {
	projectId: string;
	name: string;
	isPinned: boolean;
}) {
	const pin = usePin(projectId);
	const label = isPinned ? `Unpin ${name}` : `Pin ${name} to Home`;
	return (
		<IconButton
			variant="ghost"
			size="sm"
			rounded="card"
			aria-label={label}
			title={label}
			disabled={pin.isPending}
			onClick={() => pin.mutate(!isPinned)}
		>
			{isPinned ? <PinOff /> : <Pin />}
		</IconButton>
	);
}
