import { chakra, Input, Stack, Text } from "@chakra-ui/react";
import { CircleAlert } from "lucide-react";
import { type InputHTMLAttributes, useId } from "react";

interface FieldProps
	extends Omit<
		InputHTMLAttributes<HTMLInputElement>,
		| "id"
		| "aria-describedby"
		| "aria-invalid"
		| "className"
		| "color"
		| "size"
		| "height"
		| "width"
	> {
	label: string;
	hint?: string | undefined;
	/** The API's message for this field, shown under it and read with it. */
	error?: string | undefined;
}

/**
 * A labelled input whose hint and error are part of its accessible description.
 * The ids are wired here rather than by Chakra's Field, which discovers its
 * hint and error after rendering: the description must be right on first paint.
 */
export function Field({ label, hint, error, ...input }: FieldProps) {
	const id = useId();
	const hintId = `${id}-hint`;
	const errorId = `${id}-error`;
	const describedBy = [
		error === undefined ? null : errorId,
		hint === undefined ? null : hintId,
	]
		.filter((part) => part !== null)
		.join(" ");

	return (
		<Stack gap="1.5">
			<chakra.label htmlFor={id} fontSize="sm" fontWeight="medium">
				{label}
			</chakra.label>
			<Input
				{...input}
				id={id}
				size="lg"
				aria-invalid={error === undefined ? undefined : true}
				aria-describedby={describedBy === "" ? undefined : describedBy}
			/>
			{error === undefined ? null : (
				<Text
					id={errorId}
					display="flex"
					gap="1.5"
					alignItems="center"
					fontSize="sm"
					color="fg.error"
				>
					<CircleAlert size={16} />
					{error}
				</Text>
			)}
			{hint === undefined ? null : (
				<Text id={hintId} fontSize="sm" color="fg.muted">
					{hint}
				</Text>
			)}
		</Stack>
	);
}
