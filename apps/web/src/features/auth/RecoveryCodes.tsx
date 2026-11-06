import { Button, Checkbox, Code, Grid, Stack, Text } from "@chakra-ui/react";
import { Download } from "lucide-react";
import { useState } from "react";
import { SectionHeading } from "../../ui/SectionHeading";

interface RecoveryCodesProps {
	codes: readonly string[];
	onDone: () => void;
}

function download(codes: readonly string[]): void {
	const text = `Humani-T recovery codes\nEach code signs you in once.\n\n${codes.join("\n")}\n`;
	const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
	const link = document.createElement("a");
	link.href = url;
	link.download = "Humani-T-recovery-codes.txt";
	link.click();
	URL.revokeObjectURL(url);
}

/**
 * Recovery codes, shown once (UI_UX_SPEC §6.1). The API keeps only their
 * hashes, so this is the only time anyone can read them; going on needs an
 * explicit "I've saved them".
 */
export function RecoveryCodes({ codes, onDone }: RecoveryCodesProps) {
	const [isSaved, setSaved] = useState(false);

	return (
		<Stack gap="5">
			<SectionHeading>Save your recovery codes</SectionHeading>
			<Text>
				Each code signs you in once if you lose your passkey or phone. This is
				the only time they are shown: download them or write them down, and keep
				them somewhere safe.
			</Text>
			<Grid
				as="ul"
				listStyle="none"
				m="0"
				p="4"
				gap="2"
				templateColumns="repeat(2, minmax(0, 1fr))"
				rounded="card"
				bg="bg.subtle"
			>
				{codes.map((code) => (
					<li key={code}>
						<Code bg="transparent" fontSize="md" letterSpacing="0.04em">
							{code}
						</Code>
					</li>
				))}
			</Grid>
			<Button
				type="button"
				variant="outline"
				alignSelf="flex-start"
				onClick={() => download(codes)}
			>
				<Download />
				Download codes
			</Button>
			<Checkbox.Root
				checked={isSaved}
				onCheckedChange={(details) => setSaved(details.checked === true)}
			>
				<Checkbox.HiddenInput />
				<Checkbox.Control />
				<Checkbox.Label>I've saved my recovery codes</Checkbox.Label>
			</Checkbox.Root>
			<Button
				type="button"
				alignSelf="flex-start"
				disabled={!isSaved}
				onClick={onDone}
			>
				Continue
			</Button>
		</Stack>
	);
}
