/**
 * Project settings' Retention section (UI_UX_SPEC §6.14, §7.7): the
 * deployment's period and this project's date for every member, and Keep for
 * owners. Shown only when the deployment sets a period.
 */

import { Button, Flex, Stack, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock } from "lucide-react";
import { useApi } from "../../app/api-context";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { fullDate, type Project, shortDate } from "../projects/format";
import { SettingsSection } from "../projects/SettingsSection";
import { plural } from "../reviews/format";
import { projectRetentionQuery, useKeepProject } from "./queries";
import { retentionPolicyText } from "./words";

function When({ iso }: { iso: string }) {
	return (
		<time dateTime={iso} title={fullDate(iso)}>
			{shortDate(iso)}
		</time>
	);
}

export function RetentionSection({ project }: { project: Project }) {
	const api = useApi();
	const retention = useQuery(projectRetentionQuery(api, project.id));
	const keep = useKeepProject();

	if (retention.isError) {
		return (
			<SettingsSection title="Retention">
				<ErrorNotice
					error={retention.error}
					onRetry={() => void retention.refetch()}
				/>
			</SettingsSection>
		);
	}
	const data = retention.data;
	if (data === undefined || data.period_days === null) {
		return null;
	}
	return (
		<SettingsSection title="Retention">
			<Stack gap="3" fontSize="sm">
				<Text>{retentionPolicyText(data.period_days)}</Text>
				{keep.isError ? <ErrorNotice error={keep.error} /> : null}
				<Flex gap="4" align="center" justify="space-between" wrap="wrap">
					<Stack gap="1" maxW="md" aria-live="polite">
						{data.deletes_at === null ? null : (
							<Text>
								This project will be deleted on <When iso={data.deletes_at} />,
								in {plural(data.days_left ?? 0, "day")}.
							</Text>
						)}
						{data.kept_at === null ? null : (
							<Text color="fg.muted">
								Kept on <When iso={data.kept_at} />.
							</Text>
						)}
					</Stack>
					{project.role === "owner" ? (
						<Button
							size="sm"
							variant="outline"
							disabled={keep.isPending}
							onClick={() => keep.mutate(project.id)}
						>
							<CalendarClock />
							Keep project
						</Button>
					) : (
						<Text>Only owners can keep the project.</Text>
					)}
				</Flex>
			</Stack>
		</SettingsSection>
	);
}
