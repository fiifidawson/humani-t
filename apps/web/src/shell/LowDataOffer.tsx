/**
 * The one-time offer of low data mode (UI_UX_SPEC §13.2), shown when the browser
 * says the connection is slow or asks to save data. It never turns the mode on by
 * itself: the person chooses, and either answer ends the offer in this browser.
 */

import { Alert, Button, chakra, HStack, Text } from "@chakra-ui/react";
import { Gauge } from "lucide-react";
import { useId } from "react";
import { useLowData, useLowDataOfferAnswered } from "../app/preferences";

const SLOW_TYPES = new Set(["slow-2g", "2g", "3g"]);

/** What the Network Information API says, where the browser has it (not Firefox or Safari). */
function connectionLooksSlow(): boolean {
	const connection: unknown = Reflect.get(window.navigator, "connection");
	if (typeof connection !== "object" || connection === null) {
		return false;
	}
	const saveData: unknown = Reflect.get(connection, "saveData");
	const effectiveType: unknown = Reflect.get(connection, "effectiveType");
	return (
		saveData === true ||
		(typeof effectiveType === "string" && SLOW_TYPES.has(effectiveType))
	);
}

export function LowDataOffer() {
	const [isOn, setOn] = useLowData();
	const [isAnswered, answer] = useLowDataOfferAnswered();
	const headingId = useId();
	if (isOn || isAnswered || !connectionLooksSlow()) {
		return null;
	}
	return (
		<chakra.section aria-labelledby={headingId}>
			<Alert.Root
				colorPalette="brand"
				variant="subtle"
				rounded="0"
				px="4"
				py="3"
				alignItems="flex-start"
			>
				<Alert.Indicator>
					<Gauge />
				</Alert.Indicator>
				<Alert.Content color="fg" gap="2">
					<Alert.Title id={headingId}>Low data mode</Alert.Title>
					<Text>
						Your connection looks slow. Low data mode loads less; you can change
						it in Account.
					</Text>
					<HStack gap="2" wrap="wrap">
						<Button
							size="sm"
							onClick={() => {
								setOn(true);
								answer();
							}}
						>
							Turn on low data mode
						</Button>
						<Button size="sm" variant="outline" onClick={answer}>
							No thanks
						</Button>
					</HStack>
				</Alert.Content>
			</Alert.Root>
		</chakra.section>
	);
}
