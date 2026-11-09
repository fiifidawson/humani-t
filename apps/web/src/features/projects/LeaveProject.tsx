/**
 * Leaving a project (UI_UX_SPEC §6.14), open to every member.
 *
 * The API refuses the last owner; the page locks the control only when the
 * member list it already holds shows that, and otherwise shows the refusal.
 * Leaving is self-service, so unlike removing someone it needs no recent sign-in.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useId, useState } from "react";
import { unwrapEmpty } from "../../api/client";
import { isApiError } from "../../api/problem";
import { useApi } from "../../app/api-context";
import { membersQuery, sessionQuery } from "../../app/queries";
import { Dialog } from "../../ui/Dialog";
import { ErrorNotice } from "../../ui/ErrorNotice";
import type { Project } from "./format";
import { useLeaveForHome } from "./useLeaveForHome";

const ONLY_OWNER =
	"You're the only owner, so you can't leave. Make someone else an owner first.";

export function LeaveControl({ project }: { project: Project }) {
	const api = useApi();
	const members = useQuery(membersQuery(api, project.id));
	const reasonId = useId();
	const [isLeaving, setLeaving] = useState(false);
	const owners = (members.data ?? []).filter((m) => m.role === "owner");
	const isOnlyOwner = project.role === "owner" && owners.length === 1;

	return (
		<Stack gap="2">
			<Flex gap="4" align="center" justify="space-between" wrap="wrap">
				<Text fontSize="sm" maxW="md">
					Leave this project. You lose access straight away.
				</Text>
				<Button
					size="sm"
					variant="outline"
					colorPalette="danger"
					disabled={isOnlyOwner}
					aria-describedby={isOnlyOwner ? reasonId : undefined}
					onClick={() => setLeaving(true)}
				>
					<LogOut />
					Leave project…
				</Button>
			</Flex>
			{isOnlyOwner ? (
				<Text id={reasonId} fontSize="sm" color="fg.muted">
					{ONLY_OWNER}
				</Text>
			) : null}
			{isLeaving ? (
				<LeaveDialog project={project} onClose={() => setLeaving(false)} />
			) : null}
		</Stack>
	);
}

function LeaveDialog({
	project,
	onClose,
}: {
	project: Project;
	onClose: () => void;
}) {
	const api = useApi();
	const session = useQuery(sessionQuery(api));
	const leaveForHome = useLeaveForHome(project.id);
	const userId = session.data?.user.id;
	const leave = useMutation({
		mutationFn: (ownId: string) =>
			unwrapEmpty(
				api.DELETE("/api/v1/projects/{project_id}/members/{user_id}", {
					params: { path: { project_id: project.id, user_id: ownId } },
				}),
			),
		onSuccess: leaveForHome,
		onError: async (error) => {
			// A 404 means access had already gone: the outcome is the same as leaving.
			if (isApiError(error, "not-found")) {
				await leaveForHome();
			}
		},
	});

	return (
		<Dialog isOpen title={`Leave ${project.name}?`} onClose={onClose}>
			<Stack gap="4" fontSize="sm">
				<Text>
					You lose access to this project straight away. An owner can add you
					back later.
				</Text>
				{leave.isError && !isApiError(leave.error, "not-found") ? (
					<ErrorNotice error={leave.error} />
				) : null}
				<Flex gap="2" justify="flex-end" wrap="wrap">
					<Button size="sm" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button
						size="sm"
						colorPalette="danger"
						disabled={userId === undefined || leave.isPending}
						onClick={() => {
							if (userId !== undefined) {
								leave.mutate(userId);
							}
						}}
					>
						<LogOut />
						Leave project
					</Button>
				</Flex>
			</Stack>
		</Dialog>
	);
}
