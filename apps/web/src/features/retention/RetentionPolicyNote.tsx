/**
 * The deployment's retention period on an administrator's Account page
 * (UI_UX_SPEC §7.7): what the operator set, in the deployment's own numbers.
 * Nobody else sees it here; owners read it in Project settings.
 */

import { Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { useApi } from "../../app/api-context";
import { sessionQuery } from "../../app/queries";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { SectionHeading } from "../../ui/SectionHeading";
import { retentionPolicyQuery } from "./queries";
import { retentionPolicyText } from "./words";

function PolicyText() {
	const api = useApi();
	const policy = useQuery(retentionPolicyQuery(api));
	if (policy.isError) {
		return (
			<ErrorNotice error={policy.error} onRetry={() => void policy.refetch()} />
		);
	}
	return policy.data === undefined ? null : (
		<Text fontSize="sm">{retentionPolicyText(policy.data.period_days)}</Text>
	);
}

export function RetentionPolicyNote() {
	const api = useApi();
	const headingId = useId();
	const session = useQuery(sessionQuery(api));
	if (session.data?.user.is_admin !== true) {
		return null;
	}
	return (
		<section aria-labelledby={headingId}>
			<SectionHeading id={headingId}>Data retention</SectionHeading>
			<PolicyText />
		</section>
	);
}
