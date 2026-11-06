import { Alert, Button, Text } from "@chakra-ui/react";
import { CircleX } from "lucide-react";
import { ApiError, isApiError, userMessage } from "../api/problem";

interface ErrorNoticeProps {
	error: unknown;
	onRetry?: () => void;
}

/**
 * A failed request, in the API's words (UI_UX_SPEC §5.3.10, §11.4). The
 * request id is there for support; nothing raw ever is.
 */
export function ErrorNotice({ error, onRetry }: ErrorNoticeProps) {
	// The app-wide banner says the session ended; the page keeps what it showed.
	if (isApiError(error, "signed-out")) {
		return null;
	}
	const isKnown = error instanceof ApiError;
	const message = isKnown
		? userMessage(error)
		: "Something went wrong in this page. Reload to try again.";
	const requestId = isKnown ? error.requestId : null;

	return (
		<Alert.Root
			role="alert"
			colorPalette="danger"
			variant="subtle"
			rounded="card"
			alignItems="flex-start"
		>
			<Alert.Indicator>
				<CircleX />
			</Alert.Indicator>
			<Alert.Content gap="2" color="fg">
				<Text>{message}</Text>
				{requestId === null ? null : (
					<Text fontSize="sm" color="fg.muted">
						Request id: {requestId}
					</Text>
				)}
				{onRetry === undefined ? null : (
					<Button
						alignSelf="flex-start"
						variant="outline"
						size="sm"
						onClick={onRetry}
					>
						Try again
					</Button>
				)}
			</Alert.Content>
		</Alert.Root>
	);
}
