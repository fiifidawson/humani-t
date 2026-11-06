import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { expectNoAccessibilityViolations } from "../../test/axe";
import { renderWithBrand } from "../../test/render";
import { Composer } from "./Composer";

function Harness({
	onSend,
	unavailableReason,
}: {
	onSend: (text: string) => void;
	unavailableReason?: string;
}) {
	const [draft, setDraft] = useState("");
	return (
		<Composer
			label="Start a review"
			placeholder="What decision do you need evidence for?"
			draft={draft}
			onDraftChange={setDraft}
			onSend={onSend}
			{...(unavailableReason === undefined ? {} : { unavailableReason })}
		/>
	);
}

describe("the composer (UI_UX_SPEC §5.4)", () => {
	it("sends on Enter and clears itself", async () => {
		const onSend = vi.fn();
		const user = userEvent.setup();
		renderWithBrand(<Harness onSend={onSend} />);
		const box = screen.getByRole("textbox", { name: "Start a review" });

		await user.type(box, "  Cash or vouchers in Gao?  {Enter}");

		expect(onSend).toHaveBeenCalledWith("Cash or vouchers in Gao?");
		expect(box).toHaveValue("");
	});

	it("adds a line on Shift+Enter instead of sending", async () => {
		const onSend = vi.fn();
		const user = userEvent.setup();
		renderWithBrand(<Harness onSend={onSend} />);
		const box = screen.getByRole("textbox", { name: "Start a review" });

		await user.type(box, "First line{Shift>}{Enter}{/Shift}Second line");

		expect(onSend).not.toHaveBeenCalled();
		expect(box).toHaveValue("First line\nSecond line");
	});

	it("does not send blank text", async () => {
		const onSend = vi.fn();
		const user = userEvent.setup();
		renderWithBrand(<Harness onSend={onSend} />);

		await user.type(
			screen.getByRole("textbox", { name: "Start a review" }),
			"   {Enter}",
		);

		expect(onSend).not.toHaveBeenCalled();
	});

	// jsdom does no layout, so this checks the mechanism: the hidden sizer that
	// sets the box's height holds exactly the text, wrapped lines included.
	it("grows with its text, including long lines that wrap", async () => {
		const user = userEvent.setup();
		renderWithBrand(<Harness onSend={vi.fn()} />);
		const box = screen.getByRole("textbox", { name: "Start a review" });
		const question =
			"Should we give cash or food vouchers to displaced families when local markets are only partly working?";

		await user.type(box, question);
		expect(box.closest("[data-value]")).toHaveAttribute("data-value", question);

		await user.type(box, "{Shift>}{Enter}{/Shift}Second line");
		expect(box.closest("[data-value]")).toHaveAttribute(
			"data-value",
			`${question}\nSecond line`,
		);
	});

	it("starts one line tall", () => {
		renderWithBrand(<Harness onSend={vi.fn()} />);

		expect(
			screen.getByRole("textbox", { name: "Start a review" }),
		).toHaveAttribute("rows", "1");
	});

	it("keeps the text but explains why it cannot be sent yet", async () => {
		const onSend = vi.fn();
		const user = userEvent.setup();
		renderWithBrand(
			<Harness
				onSend={onSend}
				unavailableReason="Reviews can't be started on this server yet."
			/>,
		);
		const box = screen.getByRole("textbox", { name: "Start a review" });

		await user.type(box, "Cash or vouchers?{Enter}");

		expect(onSend).not.toHaveBeenCalled();
		expect(box).toHaveValue("Cash or vouchers?");
		expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
		expect(box).toHaveAccessibleDescription(
			expect.stringContaining("Reviews can't be started on this server yet."),
		);
	});

	it("has no accessibility violations", async () => {
		const { container } = renderWithBrand(<Harness onSend={vi.fn()} />);

		await expectNoAccessibilityViolations(container);
	});
});
