/**
 * A project's members and, for owners, adding, re-roling and removing them.
 *
 * The API decides every rule, and sends emails only to owners. The page only locks
 * what the list it holds already shows would be refused, and says why.
 */

import {
	Badge,
	Box,
	Button,
	chakra,
	Flex,
	NativeSelect,
	Stack,
	Text,
} from "@chakra-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserMinus, UserPlus } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { unwrap, unwrapEmpty } from "../../api/client";
import { fieldErrors, isApiError, userMessage } from "../../api/problem";
import type { components } from "../../api/schema.gen";
import { useApi } from "../../app/api-context";
import { keys, membersQuery, sessionQuery } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Field } from "../../ui/Field";
import { Loading } from "../../ui/Loading";
import { ReauthCancelledError, useReauth } from "../auth/ReauthDialog";

type Role = components["schemas"]["Role"];
type Member = components["schemas"]["MemberOut"];

const ROLE_NAMES: Readonly<Record<Role, string>> = {
	owner: "Owner",
	reviewer: "Reviewer",
	viewer: "Viewer",
};
const ROLES = Object.keys(ROLE_NAMES) as Role[];
const REAUTH_REASON = "Changing members needs you to sign in again.";
// The API's own words for the refusal, shown before anyone tries.
const LAST_OWNER =
	"A project needs at least one owner. Make someone else an owner first.";

const Rows = chakra("ul", {
	base: {
		listStyle: "none",
		m: "0",
		p: "0",
		rounded: "card",
		borderWidth: "1px",
		borderColor: "border",
		bg: "bg.panel",
		"& > li + li": { borderTopWidth: "1px", borderColor: "border" },
	},
});

const Row = chakra("li", {
	base: {
		display: "flex",
		flexWrap: "wrap",
		alignItems: "center",
		justifyContent: "space-between",
		gap: "3",
		px: "4",
		py: "3",
	},
});

/** Refresh everything about projects: a change to your own role changes the project too. */
function useRefreshProjects() {
	const queryClient = useQueryClient();
	return () => queryClient.invalidateQueries({ queryKey: keys.projects });
}

/** A cancelled re-authentication is the person changing their mind, not a failure. */
const isFailure = (error: unknown) => !(error instanceof ReauthCancelledError);

export function MembersSection({
	projectId,
	isOwner,
}: {
	projectId: string;
	isOwner: boolean;
}) {
	const api = useApi();
	const withReauth = useReauth();
	const refresh = useRefreshProjects();
	const members = useQuery(membersQuery(api, projectId));
	const session = useQuery(sessionQuery(api));
	const lastOwnerId = useId();
	const [failure, setFailure] = useState<unknown>(null);
	const [removing, setRemoving] = useState<Member | null>(null);

	const changeRole = useMutation({
		mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
			withReauth(REAUTH_REASON, () =>
				unwrap(
					api.PATCH("/api/v1/projects/{project_id}/members/{user_id}", {
						params: { path: { project_id: projectId, user_id: userId } },
						body: { role },
					}),
				),
			),
		onMutate: () => setFailure(null),
		onError: (error) => {
			if (isFailure(error)) {
				setFailure(error);
			}
		},
		onSettled: refresh,
	});

	if (members.isError) {
		return (
			<ErrorNotice
				error={members.error}
				onRetry={() => void members.refetch()}
			/>
		);
	}
	if (members.data === undefined) {
		return <Loading what="the members" />;
	}
	const ownerCount = members.data.filter((m) => m.role === "owner").length;
	const isLocked = (member: Member) =>
		member.role === "owner" && ownerCount <= 1;
	const showsLastOwner = isOwner && members.data.some(isLocked);

	return (
		<Stack gap="4">
			{failure === null ? null : <ErrorNotice error={failure} />}
			<Rows aria-label="Members">
				{members.data.map((member) => {
					const isYou = member.user_id === session.data?.user.id;
					return (
						<Row key={member.user_id}>
							<Box minW="0">
								<Flex gap="2" align="center" wrap="wrap">
									<Text as="span" fontWeight="medium">
										{member.display_name}
									</Text>
									{isYou ? (
										<Badge variant="outline" colorPalette="gray">
											You
										</Badge>
									) : null}
								</Flex>
								{member.email == null ? null : (
									<Text fontSize="sm" color="fg.muted" wordBreak="break-all">
										{member.email}
									</Text>
								)}
							</Box>
							{isOwner ? (
								<Flex gap="2" align="center" wrap="wrap">
									<RoleSelect
										label={`Role for ${member.display_name}`}
										value={member.role}
										isDisabled={isLocked(member) || changeRole.isPending}
										describedBy={isLocked(member) ? lastOwnerId : undefined}
										onChange={(role) =>
											changeRole.mutate({ userId: member.user_id, role })
										}
									/>
									{isYou ? null : (
										<Button
											size="sm"
											variant="outline"
											colorPalette="danger"
											aria-label={`Remove ${member.display_name}`}
											disabled={isLocked(member)}
											onClick={() => setRemoving(member)}
										>
											<UserMinus />
											Remove
										</Button>
									)}
								</Flex>
							) : (
								<Text as="span" fontSize="sm">
									{ROLE_NAMES[member.role]}
								</Text>
							)}
						</Row>
					);
				})}
			</Rows>
			{showsLastOwner ? (
				<Text id={lastOwnerId} fontSize="sm" color="fg.muted">
					{LAST_OWNER}
				</Text>
			) : null}
			{isOwner ? (
				<AddMemberForm projectId={projectId} />
			) : (
				<Text fontSize="sm">Only owners can change who's in the project.</Text>
			)}
			{removing === null ? null : (
				<RemoveDialog
					projectId={projectId}
					member={removing}
					onClose={() => setRemoving(null)}
				/>
			)}
		</Stack>
	);
}

function RoleSelect({
	label,
	value,
	isDisabled,
	describedBy,
	onChange,
	id,
	size = "sm",
}: {
	label?: string;
	value: Role;
	isDisabled?: boolean;
	describedBy?: string | undefined;
	onChange: (role: Role) => void;
	id?: string;
	size?: "sm" | "lg";
}) {
	return (
		<NativeSelect.Root size={size} width="auto" disabled={isDisabled}>
			<NativeSelect.Field
				id={id}
				aria-label={label}
				aria-describedby={describedBy}
				value={value}
				onChange={(event) => {
					const role = ROLES.find((r) => r === event.target.value);
					if (role !== undefined) {
						onChange(role);
					}
				}}
			>
				{ROLES.map((role) => (
					<option key={role} value={role}>
						{ROLE_NAMES[role]}
					</option>
				))}
			</NativeSelect.Field>
			<NativeSelect.Indicator />
		</NativeSelect.Root>
	);
}

function AddMemberForm({ projectId }: { projectId: string }) {
	const api = useApi();
	const withReauth = useReauth();
	const refresh = useRefreshProjects();
	const roleId = useId();
	const [email, setEmail] = useState("");
	const [role, setRole] = useState<Role>("reviewer");

	const add = useMutation({
		mutationFn: () =>
			withReauth(REAUTH_REASON, () =>
				unwrap(
					api.POST("/api/v1/projects/{project_id}/members", {
						params: { path: { project_id: projectId } },
						body: { email, role },
					}),
				),
			),
		onSuccess: async () => {
			setEmail("");
			await refresh();
		},
	});

	// No account and already a member are both about the email typed.
	const emailError = isApiError(add.error, "invalid", "conflict")
		? (fieldErrors(add.error)["email"] ?? userMessage(add.error))
		: undefined;
	const otherError =
		add.isError && emailError === undefined && isFailure(add.error)
			? add.error
			: null;

	const submit = (event: FormEvent) => {
		event.preventDefault();
		if (!add.isPending) {
			add.mutate();
		}
	};

	return (
		<Stack as="form" gap="3" onSubmit={submit}>
			{otherError === null ? null : <ErrorNotice error={otherError} />}
			<Flex gap="3" wrap="wrap" align="flex-start">
				<Box flex="1" minW="56">
					<Field
						label="Email"
						type="email"
						hint="They need an account here already."
						required
						autoComplete="off"
						value={email}
						error={emailError}
						onChange={(event) => setEmail(event.target.value)}
					/>
				</Box>
				<Stack gap="1.5">
					<chakra.label htmlFor={roleId} fontSize="sm" fontWeight="medium">
						Role
					</chakra.label>
					<RoleSelect id={roleId} size="lg" value={role} onChange={setRole} />
				</Stack>
			</Flex>
			<Button type="submit" alignSelf="flex-start" disabled={add.isPending}>
				<UserPlus />
				Add member
			</Button>
		</Stack>
	);
}

function RemoveDialog({
	projectId,
	member,
	onClose,
}: {
	projectId: string;
	member: Member;
	onClose: () => void;
}) {
	const api = useApi();
	const withReauth = useReauth();
	const refresh = useRefreshProjects();
	const remove = useMutation({
		mutationFn: () =>
			withReauth(REAUTH_REASON, () =>
				unwrapEmpty(
					api.DELETE("/api/v1/projects/{project_id}/members/{user_id}", {
						params: {
							path: { project_id: projectId, user_id: member.user_id },
						},
					}),
				),
			),
		onSuccess: async () => {
			onClose();
			await refresh();
		},
	});

	return (
		<Dialog isOpen title={`Remove ${member.display_name}?`} onClose={onClose}>
			<Stack gap="4" fontSize="sm">
				<Text>
					They lose access to this project straight away. You can add them again
					later.
				</Text>
				{remove.isError && isFailure(remove.error) ? (
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
						<UserMinus />
						Remove
					</Button>
				</Flex>
			</Stack>
		</Dialog>
	);
}
