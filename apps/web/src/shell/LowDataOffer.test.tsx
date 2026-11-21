import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { expectNoAccessibilityViolations } from "../test/axe";
import { renderApp } from "../test/render";
import { fakeApi } from "../test/server";

function signedIn() {
	const api = fakeApi();
	api.signInAs(api.addUser({ hasTotp: true }));
	return api;
}

/** What the browser reports through `navigator.connection` (Network Information API). */
function connectionReports(connection: {
	saveData?: boolean;
	effectiveType?: string;
}) {
	Object.defineProperty(window.navigator, "connection", {
		value: connection,
		configurable: true,
	});
}

afterEach(() => {
	Reflect.deleteProperty(window.navigator, "connection");
	document.documentElement.removeAttribute("data-low-data");
});

async function offer() {
	return screen.findByRole("region", { name: "Low data mode" });
}

describe("offering low data mode (UI_UX_SPEC §13.2)", () => {
	it("offers it when the browser asks to save data, and turns it on only when chosen", async () => {
		signedIn();
		connectionReports({ saveData: true, effectiveType: "4g" });
		const { user } = renderApp("/");

		const notice = await offer();
		expect(document.documentElement).not.toHaveAttribute("data-low-data");

		await user.click(
			within(notice).getByRole("button", { name: "Turn on low data mode" }),
		);

		expect(document.documentElement).toHaveAttribute("data-low-data");
		expect(window.localStorage.getItem("humani-t:low-data")).toBe("true");
		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: "Low data mode" }),
			).not.toBeInTheDocument(),
		);
	});

	it.each(["slow-2g", "2g", "3g"])(
		"offers it on a slow connection (%s)",
		async (effectiveType) => {
			signedIn();
			connectionReports({ saveData: false, effectiveType });
			renderApp("/");

			expect(await offer()).toBeInTheDocument();
		},
	);

	it("does not offer it on a fast connection, or when the browser does not say", async () => {
		signedIn();
		connectionReports({ saveData: false, effectiveType: "4g" });
		const fast = renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });
		expect(
			screen.queryByRole("region", { name: "Low data mode" }),
		).not.toBeInTheDocument();
		fast.unmount();

		Reflect.deleteProperty(window.navigator, "connection");
		renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });
		expect(
			screen.queryByRole("region", { name: "Low data mode" }),
		).not.toBeInTheDocument();
	});

	it("offers it once: after No thanks it does not come back, and stays off", async () => {
		signedIn();
		connectionReports({ saveData: true });
		const first = renderApp("/");

		await first.user.click(
			within(await offer()).getByRole("button", { name: "No thanks" }),
		);
		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: "Low data mode" }),
			).not.toBeInTheDocument(),
		);
		first.unmount();

		renderApp("/");
		await screen.findByRole("navigation", { name: "Main" });
		expect(
			screen.queryByRole("region", { name: "Low data mode" }),
		).not.toBeInTheDocument();
		expect(document.documentElement).not.toHaveAttribute("data-low-data");
	});

	it("is not offered when low data mode is already on", async () => {
		signedIn();
		window.localStorage.setItem("humani-t:low-data", "true");
		connectionReports({ saveData: true });
		renderApp("/");

		await screen.findByRole("navigation", { name: "Main" });
		expect(
			screen.queryByRole("region", { name: "Low data mode" }),
		).not.toBeInTheDocument();
	});

	it("has no accessibility violations while it is offered", async () => {
		signedIn();
		connectionReports({ saveData: true });
		const { container } = renderApp("/");
		await offer();

		await expectNoAccessibilityViolations(container);
	});
});
