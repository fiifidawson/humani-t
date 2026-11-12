/**
 * A project's documents and the owner's privacy review: upload, status, correcting
 * and approving or rejecting the masked text, and deleting.
 *
 * Members see the masked text only. The original is requested only when an owner
 * opens a document waiting for review, and the API records each request. Actions
 * are hidden by role here, and the API refuses them anyway.
 */

import {
	Alert,
	Box,
	Button,
	chakra,
	Flex,
	HStack,
	Stack,
	Table,
	Text,
	VisuallyHidden,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import {
	Check,
	CircleX,
	Eye,
	LoaderCircle,
	Pause,
	Trash2,
	TriangleAlert,
	Upload,
	X,
} from "lucide-react";
import { type DragEvent, useEffect, useId, useRef, useState } from "react";
import { unwrap, unwrapEmpty } from "../../api/client";
import { isApiError } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import {
	type DocumentListing,
	type DocumentStatus,
	documentReviewQuery,
	documentsQuery,
	isInProgress,
	maskedDocumentQuery,
	type ProjectDocument,
	uploadDocument,
} from "../../app/documentQueries";
import {
	keys,
	membersQuery,
	projectQuery,
	sessionQuery,
} from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { FormError } from "../../ui/FormError";
import { Loading } from "../../ui/Loading";
import { Page } from "../../ui/Page";
import { SectionHeading } from "../../ui/SectionHeading";
import { MaskedText } from "../reviews/MaskedText";
import { maskedCounts, totalMasked } from "../reviews/masking";
import { formatSize, statusWords } from "./format";
import { MaskingCorrections } from "./MaskingReview";

/** How often the list is fetched again while a document is being processed. */
export const POLL_INTERVAL_MS = 3000;

type DocumentType = components["schemas"]["DocumentType"];

const ROUTE = "/signed-in/p/$projectId/documents";

const FILE_TYPES: Readonly<
	Record<DocumentType, { words: string; extension: string; mime: string }>
> = {
	pdf: { words: "PDF", extension: ".pdf", mime: "application/pdf" },
	docx: {
		words: "Word (.docx)",
		extension: ".docx",
		mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	},
	txt: { words: "plain text", extension: ".txt", mime: "text/plain" },
	md: { words: "Markdown", extension: ".md", mime: "text/markdown" },
};

const OR_LIST = new Intl.ListFormat("en-GB", { type: "disjunction" });

/** What has been masked can be read; anything earlier has no masked text yet. */
const HAS_MASKED_TEXT: readonly DocumentStatus[] = [
	"awaiting_review",
	"approved",
	"rejected",
];

function StatusIcon({ status }: { status: DocumentStatus }) {
	if (status === "failed") {
		return (
			<Box as="span" color="fg.error" display="inline-flex">
				<CircleX size={16} />
			</Box>
		);
	}
	const Icon =
		status === "awaiting_review"
			? Pause
			: status === "approved"
				? Check
				: status === "rejected"
					? X
					: LoaderCircle;
	return (
		<Box as="span" color="fg.muted" display="inline-flex">
			<Icon size={16} />
		</Box>
	);
}

// The input sits inside its label, so a click anywhere on the button opens the
// file picker and the input's own focus ring is drawn on the label.
const FileLabel = chakra("label", {
	base: {
		cursor: "pointer",
		"&:has(input:focus-visible)": {
			outline: "2px solid",
			outlineColor: "focusRing",
			outlineOffset: "2px",
		},
	},
});

function UploadArea({
	listing,
	projectId,
}: {
	listing: DocumentListing;
	projectId: string;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const [isDragging, setDragging] = useState(false);
	const [refusal, setRefusal] = useState<string | null>(null);
	const [uploaded, setUploaded] = useState<string | null>(null);
	const maxBytes = listing.usage.max_file_bytes;
	const types = listing.accepted_types.map((type) => FILE_TYPES[type]);

	const upload = useMutation({
		mutationFn: (file: File) => uploadDocument(api, projectId, file),
		onSuccess: async (document) => {
			setUploaded(`${document.name} is uploaded. It's being checked now.`);
			await queryClient.invalidateQueries({
				queryKey: keys.documents(projectId),
			});
		},
	});

	const send = (files: FileList | null) => {
		setRefusal(null);
		setUploaded(null);
		upload.reset();
		const file = files?.[0];
		if (files === null || file === undefined) {
			return;
		}
		if (files.length > 1) {
			setRefusal("Upload one file at a time.");
		} else if (file.size > maxBytes) {
			setRefusal(
				`${file.name} is over ${formatSize(maxBytes)}, the largest file allowed.`,
			);
		} else {
			upload.mutate(file);
		}
	};

	const onDrop = (event: DragEvent) => {
		event.preventDefault();
		setDragging(false);
		send(event.dataTransfer.files);
	};

	return (
		<Stack gap="3">
			<Stack
				data-testid="drop-zone"
				align="center"
				gap="3"
				p="6"
				textAlign="center"
				rounded="card"
				borderWidth="2px"
				borderStyle="dashed"
				borderColor={isDragging ? "focusRing" : "border.emphasized"}
				bg={isDragging ? "brand.subtle" : "bg.panel"}
				onDragOver={(event) => {
					event.preventDefault();
					setDragging(true);
				}}
				onDragLeave={() => setDragging(false)}
				onDrop={onDrop}
			>
				<Text>Drag a file here, or</Text>
				<Button asChild disabled={upload.isPending}>
					<FileLabel>
						<Upload />
						Choose a file
						<VisuallyHidden asChild>
							<input
								type="file"
								accept={[
									...types.map((t) => t.extension),
									...types.map((t) => t.mime),
								].join(",")}
								disabled={upload.isPending}
								onChange={(event) => {
									send(event.target.files);
									// Cleared, so choosing the same file again uploads it again.
									event.target.value = "";
								}}
							/>
						</VisuallyHidden>
					</FileLabel>
				</Button>
				<Text fontSize="sm" color="fg.muted">
					{`${OR_LIST.format(types.map((t) => t.words))}, up to ${formatSize(maxBytes)}.`}
				</Text>
			</Stack>
			<Box aria-live="polite">
				{upload.isPending ? (
					<Text fontSize="sm">Uploading {upload.variables.name}…</Text>
				) : null}
				{uploaded === null ? null : <Text fontSize="sm">{uploaded}</Text>}
			</Box>
			{refusal === null ? null : (
				<FormError role="alert" icon={<CircleX size={16} />}>
					{refusal}
				</FormError>
			)}
			{upload.isError ? <ErrorNotice error={upload.error} /> : null}
		</Stack>
	);
}

function DocumentRows({
	documents,
	isOwner,
	uploaderName,
	onOpen,
	onDelete,
}: {
	documents: readonly ProjectDocument[];
	isOwner: boolean;
	uploaderName: (userId: string) => string;
	onOpen: (documentId: string) => void;
	onDelete: (document: ProjectDocument) => void;
}) {
	return (
		<Box overflowX="auto">
			<Table.Root size="sm" aria-label="Documents">
				<Table.Header>
					<Table.Row>
						<Table.ColumnHeader>Tag</Table.ColumnHeader>
						<Table.ColumnHeader>Name</Table.ColumnHeader>
						<Table.ColumnHeader>Size</Table.ColumnHeader>
						<Table.ColumnHeader>Uploaded by</Table.ColumnHeader>
						<Table.ColumnHeader>Status</Table.ColumnHeader>
						<Table.ColumnHeader>
							<VisuallyHidden>Actions</VisuallyHidden>
						</Table.ColumnHeader>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{documents.map((document) => {
						const label = `${document.tag} ${document.name}`;
						const canDecide = isOwner && document.status === "awaiting_review";
						return (
							<Table.Row key={document.id} verticalAlign="top">
								<Table.Cell fontWeight="medium">{document.tag}</Table.Cell>
								<Table.Cell overflowWrap="anywhere" minW="40">
									{document.name}
								</Table.Cell>
								<Table.Cell whiteSpace="nowrap">
									{formatSize(document.size_bytes)}
								</Table.Cell>
								<Table.Cell>{uploaderName(document.uploaded_by)}</Table.Cell>
								<Table.Cell>
									<Stack gap="1">
										<HStack gap="1.5">
											<StatusIcon status={document.status} />
											<span data-testid="status">
												{statusWords(document.status)}
											</span>
										</HStack>
										{document.failure_message == null ? null : (
											<Text fontSize="sm" color="fg.muted">
												{document.failure_message}
											</Text>
										)}
										{document.is_truncated ? (
											<Text fontSize="sm" color="fg.muted">
												Only the first part of this file was read.
											</Text>
										) : null}
									</Stack>
								</Table.Cell>
								<Table.Cell>
									<HStack gap="2" justify="flex-end" wrap="wrap">
										{!HAS_MASKED_TEXT.includes(
											document.status,
										) ? null : canDecide ? (
											<Button
												size="sm"
												aria-label={`Review ${label}`}
												onClick={() => onOpen(document.id)}
											>
												Review
											</Button>
										) : (
											<Button
												size="sm"
												variant="outline"
												aria-label={`See masked text ${label}`}
												onClick={() => onOpen(document.id)}
											>
												<Eye />
												See masked text
											</Button>
										)}
										{isOwner ? (
											<Button
												size="sm"
												variant="outline"
												colorPalette="danger"
												aria-label={`Delete ${label}`}
												onClick={() => onDelete(document)}
											>
												<Trash2 />
												Delete
											</Button>
										) : null}
									</HStack>
								</Table.Cell>
							</Table.Row>
						);
					})}
				</Table.Body>
			</Table.Root>
		</Box>
	);
}

function DeleteDialog({
	projectId,
	document,
	onClose,
	onDeleted,
}: {
	projectId: string;
	document: ProjectDocument;
	onClose: () => void;
	onDeleted: (message: string) => void;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const label = `${document.tag} ${document.name}`;

	const finish = async (message: string) => {
		onDeleted(message);
		await queryClient.invalidateQueries({
			queryKey: keys.documents(projectId),
		});
	};

	const remove = useMutation({
		mutationFn: () =>
			unwrapEmpty(
				api.DELETE("/api/v1/projects/{project_id}/documents/{document_id}", {
					params: {
						path: { project_id: projectId, document_id: document.id },
					},
				}),
			),
		onSuccess: () => finish(`${label} is deleted.`),
		// Another owner got there first: what the person asked for is done.
		onError: async (error) => {
			if (isApiError(error, "not-found")) {
				await finish(`${label} was already deleted.`);
			}
		},
	});

	return (
		<Dialog isOpen title={`Delete ${label}?`} onClose={onClose}>
			<Stack gap="4" fontSize="sm">
				<Text>
					{`This deletes the file and its text. A brief that already cites ${document.tag} keeps the passages it quoted. This can't be undone.`}
				</Text>
				{remove.isError && !isApiError(remove.error, "not-found") ? (
					<ErrorNotice error={remove.error} />
				) : null}
				<Flex gap="2" justify="flex-end" wrap="wrap">
					<Button size="sm" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button
						size="sm"
						colorPalette="danger"
						disabled={remove.isPending}
						onClick={() => remove.mutate()}
					>
						<Trash2 />
						Delete
					</Button>
				</Flex>
			</Stack>
		</Dialog>
	);
}

type Decision = "approve" | "reject";

function MaskedReview({
	projectId,
	document,
	isOwner,
	onClose,
}: {
	projectId: string;
	document: ProjectDocument;
	isOwner: boolean;
	onClose: () => void;
}) {
	const api = useApi();
	const queryClient = useQueryClient();
	const headingId = useId();
	const heading = useRef<HTMLHeadingElement>(null);
	const [outcome, setOutcome] = useState<string | null>(null);
	const canDecide = isOwner && document.status === "awaiting_review";
	const review = useQuery({
		...documentReviewQuery(api, projectId, document.id),
		enabled: canDecide,
	});
	const masked = useQuery({
		...maskedDocumentQuery(api, projectId, document.id),
		enabled: !canDecide,
	});
	const shown = canDecide ? review : masked;
	const version = shown.data?.mask_version;
	const label = `${document.tag} ${document.name}`;
	const title = canDecide
		? `Privacy review: ${label}`
		: `What the model will see: ${label}`;

	useEffect(() => {
		heading.current?.focus();
	}, []);

	const decide = useMutation({
		mutationFn: ({
			decision,
			version,
		}: {
			decision: Decision;
			version: number;
		}) => {
			const params = {
				path: { project_id: projectId, document_id: document.id },
			};
			const body = { version };
			return unwrap(
				decision === "approve"
					? api.POST(
							"/api/v1/projects/{project_id}/documents/{document_id}/approve",
							{ params, body },
						)
					: api.POST(
							"/api/v1/projects/{project_id}/documents/{document_id}/reject",
							{ params, body },
						),
			);
		},
		onSuccess: async (decided) => {
			setOutcome(
				decided.status === "approved"
					? `Approved. The brief can now cite it as ${decided.tag}.`
					: "Rejected. It won't be used.",
			);
			await queryClient.invalidateQueries({
				queryKey: keys.documents(projectId),
			});
		},
		// Someone else corrected or decided it meanwhile: show what is current.
		onError: async (error) => {
			if (isApiError(error, "conflict")) {
				await Promise.all([
					queryClient.invalidateQueries({
						queryKey: keys.documents(projectId),
					}),
					queryClient.invalidateQueries({
						queryKey: keys.documentReview(projectId, document.id),
					}),
				]);
			}
		},
	});

	return (
		<Stack
			as="section"
			aria-labelledby={headingId}
			gap="4"
			p={{ base: "4", md: "6" }}
			rounded="card"
			borderWidth="1px"
			borderColor="border"
			bg="bg.panel"
		>
			<Flex gap="3" align="flex-start" justify="space-between">
				<Text
					as="h3"
					id={headingId}
					ref={heading}
					tabIndex={-1}
					fontFamily="heading"
					fontWeight="semibold"
					fontSize="xl"
					overflowWrap="anywhere"
				>
					{title}
				</Text>
				<Button size="sm" variant="outline" flex="none" onClick={onClose}>
					<X />
					Close
				</Button>
			</Flex>
			<Alert.Root
				colorPalette="caveat"
				variant="subtle"
				rounded="card"
				alignItems="flex-start"
			>
				<Alert.Indicator>
					<TriangleAlert />
				</Alert.Indicator>
				<Alert.Content color="fg">
					<Text>
						{canDecide && review.data !== undefined
							? review.data.notice
							: "Masking is not anonymity. Codes, numbers and rare details can still point to a person, and text that is only in images was not read."}
					</Text>
				</Alert.Content>
			</Alert.Root>
			<Text>
				{totalMasked(document.masked_items) === 0
					? "Nothing was masked."
					: `Masked: ${maskedCounts(document.masked_items)}.`}
			</Text>
			{shown.isPending ? <Loading what="the text" /> : null}
			{shown.isError ? (
				<ErrorNotice error={shown.error} onRetry={() => void shown.refetch()} />
			) : null}
			{canDecide && review.data !== undefined ? (
				<MaskingCorrections projectId={projectId} review={review.data} />
			) : null}
			{canDecide || masked.data === undefined ? null : (
				<Box
					p="4"
					rounded="card"
					borderWidth="1px"
					borderColor="border"
					bg="bg"
				>
					<MaskedText text={masked.data.masked_text} />
					{masked.data.is_truncated ? (
						<Text mt="3" fontSize="sm" color="fg.muted">
							Only the first part of this file was read.
						</Text>
					) : null}
				</Box>
			)}
			{canDecide ? (
				<Stack gap="3">
					<Text>
						{`If you approve it, this masked text becomes evidence the brief can cite as ${document.tag}. If you reject it, it is never used.`}
					</Text>
					{version === undefined ? null : (
						<HStack gap="3" wrap="wrap">
							<Button
								disabled={decide.isPending}
								onClick={() => decide.mutate({ decision: "approve", version })}
							>
								<Check />
								Approve
							</Button>
							<Button
								variant="outline"
								disabled={decide.isPending}
								onClick={() => decide.mutate({ decision: "reject", version })}
							>
								<X />
								Reject
							</Button>
						</HStack>
					)}
				</Stack>
			) : null}
			{!isOwner && document.status === "awaiting_review" ? (
				<Text color="fg.muted">
					Only owners can approve or reject documents.
				</Text>
			) : null}
			<Box aria-live="polite">
				{outcome === null ? null : <Text fontWeight="medium">{outcome}</Text>}
			</Box>
			{decide.isError ? <ErrorNotice error={decide.error} /> : null}
		</Stack>
	);
}

export function DocumentsPage() {
	const api = useApi();
	const navigate = useNavigate();
	const { projectId } = useParams({ from: ROUTE });
	const { document: openId } = useSearch({ from: ROUTE });
	const project = useQuery(projectQuery(api, projectId));
	const session = useQuery(sessionQuery(api));
	const members = useQuery(membersQuery(api, projectId));
	const listing = useQuery(documentsQuery(api, projectId, POLL_INTERVAL_MS));
	const isOwner = project.data?.role === "owner";
	const [deleting, setDeleting] = useState<ProjectDocument | null>(null);
	const [deleted, setDeleted] = useState<string | null>(null);

	const open = (documentId: string | undefined) =>
		void navigate({
			to: "/p/$projectId/documents",
			params: { projectId },
			search: { document: documentId },
		});

	const uploaderName = (userId: string): string => {
		if (userId === session.data?.user.id) {
			return "You";
		}
		const member = members.data?.find((m) => m.user_id === userId);
		if (member !== undefined) {
			return member.display_name;
		}
		return members.isPending ? "…" : "A former member";
	};

	const opened = listing.data?.documents.find((d) => d.id === openId);
	const isProcessing = listing.data?.documents.some(isInProgress) ?? false;

	return (
		<Box flex="1" minH="0" overflowY="auto" bg="bg.subtle">
			<Page>
				<Stack as="section" aria-labelledby="documents-title" gap="5">
					<SectionHeading id="documents-title">Documents</SectionHeading>
					<Text maxW="3xl">
						Your team's own files. Each one is checked for viruses, read and
						masked, then an owner checks the masking. Only approved documents
						are used, and the brief cites them as D1, D2 and so on.
					</Text>
					{listing.isPending || project.isPending ? (
						<Loading what="the documents" />
					) : null}
					{listing.isError ? (
						<ErrorNotice
							error={listing.error}
							onRetry={() => void listing.refetch()}
						/>
					) : null}
					{listing.data === undefined || project.data === undefined ? null : (
						<>
							{!isOwner ? (
								<Text color="fg.muted">Only owners can upload documents.</Text>
							) : listing.data.uploads_enabled ? (
								<UploadArea listing={listing.data} projectId={projectId} />
							) : (
								<Text color="fg.muted">
									{listing.data.uploads_disabled_reason ??
										"Uploads are switched off on this server."}
								</Text>
							)}
							<Box aria-live="polite">
								{deleted === null ? null : <Text>{deleted}</Text>}
							</Box>
							{listing.data.documents.length === 0 ? (
								<Text>No documents yet.</Text>
							) : (
								<DocumentRows
									documents={listing.data.documents}
									isOwner={isOwner}
									uploaderName={uploaderName}
									onOpen={open}
									onDelete={(document) => {
										setDeleted(null);
										setDeleting(document);
									}}
								/>
							)}
							{isProcessing ? (
								<Text fontSize="sm" color="fg.muted">
									This page updates by itself while files are being checked.
								</Text>
							) : null}
						</>
					)}
				</Stack>
				{opened === undefined ? null : (
					<MaskedReview
						key={opened.id}
						projectId={projectId}
						document={opened}
						isOwner={isOwner}
						onClose={() => open(undefined)}
					/>
				)}
				{deleting === null ? null : (
					<DeleteDialog
						projectId={projectId}
						document={deleting}
						onClose={() => setDeleting(null)}
						onDeleted={(message) => {
							setDeleting(null);
							setDeleted(message);
							if (openId === deleting.id) {
								open(undefined);
							}
						}}
					/>
				)}
			</Page>
		</Box>
	);
}
