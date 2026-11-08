/**
 * What Humani-T is doing while its reply is on the way: dots on its side of the
 * chat, the work in plain words, and how long it has taken once that is worth
 * saying. A reasoning model often needs 20 to 60 seconds.
 */

import { Box, Flex, Text } from "@chakra-ui/react";
import { useEffect, useState } from "react";

const SHOW_TIME_AFTER = 5;
const SAY_IT_IS_SLOW_AFTER = 15;

function useSecondsSince(): number {
	const [seconds, setSeconds] = useState(0);
	useEffect(() => {
		const started = Date.now();
		const timer = setInterval(
			() => setSeconds(Math.floor((Date.now() - started) / 1000)),
			1000,
		);
		return () => clearInterval(timer);
	}, []);
	return seconds;
}

function Dots() {
	return (
		<Flex
			aria-hidden="true"
			gap="1"
			css={{
				"@keyframes reply-dot": {
					"0%, 80%, 100%": { opacity: 0.25, transform: "translateY(0)" },
					"40%": { opacity: 1, transform: "translateY(-2px)" },
				},
				"& > span": { animation: "reply-dot 1.2s ease-in-out infinite" },
				"& > span:nth-of-type(2)": { animationDelay: "0.15s" },
				"& > span:nth-of-type(3)": { animationDelay: "0.3s" },
			}}
		>
			<Box as="span" boxSize="1.5" rounded="full" bg="brand.solid" />
			<Box as="span" boxSize="1.5" rounded="full" bg="brand.solid" />
			<Box as="span" boxSize="1.5" rounded="full" bg="brand.solid" />
		</Flex>
	);
}

/** `doing` without its ellipsis: "Drafting the search plan". */
export function PendingReply({ doing }: { doing: string }) {
	const seconds = useSecondsSince();
	return (
		<Flex
			role="status"
			data-role="humani-t"
			align="center"
			gap="3"
			alignSelf="flex-start"
			maxW="85%"
			px="4"
			py="2.5"
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
			fontSize="sm"
		>
			<Dots />
			<Text as="span" color="fg">
				{doing}…
			</Text>
			{/* The time changes every second: shown, never announced. */}
			{seconds >= SHOW_TIME_AFTER ? (
				<Text as="span" aria-hidden="true" color="fg.muted" fontSize="xs">
					{seconds} s
					{seconds >= SAY_IT_IS_SLOW_AFTER ? " · this can take a minute" : ""}
				</Text>
			) : null}
		</Flex>
	);
}
