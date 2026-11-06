import { Dialog as ChakraDialog, Portal } from "@chakra-ui/react";
import type { ReactNode } from "react";

interface DialogProps {
	isOpen: boolean;
	title: string;
	/** Esc, a click outside, or anything else that closes the dialog without its own buttons. */
	onClose: () => void;
	children: ReactNode;
}

/** A modal: focus trapped inside, the page behind hidden from assistive technology, Esc closes. */
export function Dialog({ isOpen, title, onClose, children }: DialogProps) {
	return (
		<ChakraDialog.Root
			open={isOpen}
			onOpenChange={(details) => {
				if (!details.open) {
					onClose();
				}
			}}
			placement="center"
			lazyMount
			unmountOnExit
		>
			<Portal>
				<ChakraDialog.Backdrop bg="scrim" />
				<ChakraDialog.Positioner px="4">
					<ChakraDialog.Content bg="bg.panel" rounded="card" p="2" maxW="md">
						<ChakraDialog.Header>
							<ChakraDialog.Title
								fontFamily="heading"
								fontWeight="semibold"
								fontSize="2xl"
							>
								{title}
							</ChakraDialog.Title>
						</ChakraDialog.Header>
						<ChakraDialog.Body pb="6">{children}</ChakraDialog.Body>
					</ChakraDialog.Content>
				</ChakraDialog.Positioner>
			</Portal>
		</ChakraDialog.Root>
	);
}
