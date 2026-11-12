/**
 * The canvas (UI_UX_SPEC §4.1, §4.2): one large view beside the chat. From
 * 1024 px it sits next to the chat; below that it covers it, 90% wide on a
 * tablet and the whole screen on a phone. Its heading takes focus when it
 * opens; ✕ or Esc closes it and gives the chat back. A source panel, when
 * open, takes Esc first (it listens in the capture phase and claims the key).
 */

import {
	Box,
	Button,
	chakra,
	Flex,
	Heading,
	IconButton,
} from "@chakra-ui/react";
import { useNavigate } from "@tanstack/react-router";
import { FileDown, Maximize2, Minimize2, Printer, X } from "lucide-react";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useId,
	useRef,
	useState,
} from "react";
import { HEADER_HEIGHT } from "../../shell/railStyles";
import { DisclosureButton } from "../../ui/DisclosureButton";
import { useMediaQuery, WIDE_SCREEN } from "../../ui/useMediaQuery";
import { useCanvasWidth } from "./canvasWidth";
import type { Download } from "./exports";
import { useReview } from "./reviewContext";

/** Esc inside a dialog belongs to the dialog. */
const isInDialog = (target: EventTarget | null) =>
	target instanceof Element && target.closest("[role=dialog]") !== null;

export function Canvas({
	title,
	children,
	panel,
	downloads,
}: {
	title: string;
	children: ReactNode;
	/** The source panel (§6.10), over the canvas's right edge, leaving it open beneath. */
	panel?: ReactNode;
	/** Files of what the canvas shows, behind a Download button in its header. */
	downloads?: readonly Download[];
}) {
	const [isDownloadsOpen, setDownloadsOpen] = useState(false);
	const { project, review } = useReview();
	const navigate = useNavigate();
	const heading = useRef<HTMLHeadingElement>(null);
	const id = useId();
	const { isFullWidth, setFullWidth } = useCanvasWidth();
	const isWide = useMediaQuery(WIDE_SCREEN);

	const close = useCallback(
		() =>
			void navigate({
				to: "/p/$projectId/r/$reviewId",
				params: { projectId: project.id, reviewId: review.id },
			}),
		[navigate, project.id, review.id],
	);

	useEffect(() => {
		heading.current?.focus({ preventScroll: true });
	}, []);

	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			if (
				event.key === "Escape" &&
				!event.defaultPrevented &&
				!isInDialog(event.target)
			) {
				// At full width, Esc gives the chat back first.
				if (isFullWidth) {
					setFullWidth(false);
				} else {
					close();
				}
			}
		};
		document.addEventListener("keydown", onKeyDown);
		return () => document.removeEventListener("keydown", onKeyDown);
	}, [close, isFullWidth, setFullWidth]);

	return (
		<Flex
			as="section"
			aria-labelledby={id}
			direction="column"
			minH="0"
			bg="bg"
			borderLeftWidth="1px"
			borderColor="border"
			flex={{ lg: "1 1 0" }}
			minW={{ lg: "480px" }}
			position={{ base: "fixed", lg: "relative" }}
			top={{ base: HEADER_HEIGHT, lg: "auto" }}
			right="0"
			bottom="0"
			w={{ base: "full", md: "90%", lg: "auto" }}
			zIndex={{ base: "25", lg: "auto" }}
			boxShadow={{ base: "lg", lg: "none" }}
		>
			<Flex
				as="header"
				align="center"
				gap="3"
				px={{ base: "4", md: "6" }}
				py="3"
				borderBottomWidth="1px"
				borderColor="border"
			>
				<Heading
					as="h2"
					id={id}
					ref={heading}
					tabIndex={-1}
					flex="1"
					minW="0"
					fontFamily="heading"
					fontWeight="semibold"
					fontSize={{ base: "lg", md: "xl" }}
					lineHeight="1.2"
				>
					{title}
				</Heading>
				{downloads === undefined ? null : (
					<DisclosureButton
						isOpen={isDownloadsOpen}
						controls={`${id}-downloads`}
						onToggle={() => setDownloadsOpen(!isDownloadsOpen)}
					>
						Download
					</DisclosureButton>
				)}
				{isWide ? (
					<IconButton
						variant="ghost"
						rounded="card"
						size="sm"
						aria-label="Full width"
						aria-pressed={isFullWidth}
						title={isFullWidth ? "Show the chat again" : "Use the full width"}
						onClick={() => setFullWidth(!isFullWidth)}
					>
						{isFullWidth ? <Minimize2 /> : <Maximize2 />}
					</IconButton>
				) : null}
				<IconButton
					variant="ghost"
					rounded="card"
					size="sm"
					aria-label="Close"
					onClick={close}
				>
					<X />
				</IconButton>
			</Flex>
			{downloads !== undefined && isDownloadsOpen ? (
				<chakra.ul
					id={`${id}-downloads`}
					role="list"
					aria-label="Downloads"
					display="flex"
					flexWrap="wrap"
					gap="2"
					listStyleType="none"
					m="0"
					px={{ base: "4", md: "6" }}
					py="3"
					borderBottomWidth="1px"
					borderColor="border"
					bg="bg.subtle"
				>
					{downloads.map((file) => (
						<li key={file.href}>
							<Button asChild size="xs" variant="outline">
								{file.isPrintable ? (
									<a href={file.href} target="_blank" rel="noopener noreferrer">
										<Printer />
										{file.label}
									</a>
								) : (
									<a href={file.href} download>
										<FileDown />
										{file.label}
									</a>
								)}
							</Button>
						</li>
					))}
				</chakra.ul>
			) : null}
			{/* position: relative keeps visually hidden text, which is absolutely
			    positioned, inside this scroller; without it the page itself scrolls. */}
			<Box
				flex="1"
				minH="0"
				position="relative"
				overflowY="auto"
				px={{ base: "4", md: "6" }}
				py="5"
				fontSize="sm"
			>
				{children}
			</Box>
			{panel}
		</Flex>
	);
}

/** A sub-heading inside a canvas, after the brand's small-caps section label. */
export function CanvasHeading({
	id,
	children,
}: {
	id?: string;
	children: ReactNode;
}) {
	return (
		<Heading
			as="h3"
			id={id}
			fontFamily="body"
			fontSize="xs"
			fontWeight="normal"
			textTransform="uppercase"
			letterSpacing="0.08em"
			color="fg.muted"
			mt="6"
			mb="2"
		>
			{children}
		</Heading>
	);
}
