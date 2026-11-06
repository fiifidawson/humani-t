import { setupServer } from "msw/node";
import { FakeApi } from "./fakeApi";

export const server = setupServer();

let current = new FakeApi();

/** Give each test a fresh fake API; called from the setup file. */
export function resetFakeApi(): void {
	current = new FakeApi();
	server.resetHandlers(...current.handlers());
}

/** The fake API behind the current test, for arranging state and checking requests. */
export function fakeApi(): FakeApi {
	return current;
}
