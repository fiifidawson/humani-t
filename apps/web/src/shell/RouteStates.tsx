import { Box, Link as ChakraLink, Text } from "@chakra-ui/react";
import { Link, useRouter } from "@tanstack/react-router";
import { ErrorNotice } from "../ui/ErrorNotice";
import { linkStyle } from "../ui/LinkButton";
import { Page } from "../ui/Page";
import { PageHeading } from "../ui/PageHeading";

export function NotFoundPage() {
	return (
		<Box as="main" id="main">
			<Page>
				<PageHeading title="Page not found" />
				<Text>There is nothing at this address.</Text>
				<ChakraLink asChild css={linkStyle} alignSelf="flex-start">
					<Link to="/">Go home</Link>
				</ChakraLink>
			</Page>
		</Box>
	);
}

/** A route's loading or guard failed in a way no screen handles itself. */
export function RouteErrorPage({ error }: { error: unknown }) {
	const router = useRouter();
	return (
		<Page>
			<PageHeading title="Something went wrong" />
			<ErrorNotice error={error} onRetry={() => void router.invalidate()} />
		</Page>
	);
}

export function RoutePending() {
	return (
		<Box aria-busy="true">
			<Page>
				<Text color="fg.muted">Loading…</Text>
			</Page>
		</Box>
	);
}
