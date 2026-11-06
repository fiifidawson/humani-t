import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import { FakeEventSource } from "./fakeEventSource";
import { resetFakeApi, server } from "./server";

// A file's first whole-app render (router, queries, MSW, cold module
// transforms) took 9 s on a loaded Windows machine. Waiting longer never
// turns a wrong answer into a pass: it only stops slow machines failing.
configure({ asyncUtilTimeout: 15_000 });

// jsdom has no modal dialogs. This keeps what the app relies on: `open` is
// set and cleared, and closing fires `close`. Focus trapping and inertness are
// the browser's job and are checked by hand (walkthrough 13).
if (
	typeof HTMLDialogElement !== "undefined" &&
	!("showModal" in HTMLDialogElement.prototype)
) {
	Object.assign(HTMLDialogElement.prototype, {
		showModal(this: HTMLDialogElement) {
			this.setAttribute("open", "");
		},
		close(this: HTMLDialogElement) {
			if (this.hasAttribute("open")) {
				this.removeAttribute("open");
				this.dispatchEvent(new Event("close"));
			}
		},
	});
}

function clearCookies(): void {
	for (const pair of document.cookie.split(";")) {
		const name = pair.split("=")[0]?.trim();
		if (name) {
			// biome-ignore lint/suspicious/noDocumentCookie: clearing what the fake API set, between tests
			document.cookie = `${name}=; Secure; Path=/; Max-Age=0`;
		}
	}
}

beforeAll(() => {
	server.listen({ onUnhandledFrame: "error" });
});

beforeEach(() => {
	resetFakeApi();
	clearCookies();
	window.localStorage.clear();
	document.documentElement.removeAttribute("data-theme");
	// jsdom has no EventSource. Any screen that follows a run gets the fake,
	// which a test drives with FakeEventSource.latest().
	FakeEventSource.reset();
	vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
	cleanup();
});

afterAll(() => {
	server.close();
});
