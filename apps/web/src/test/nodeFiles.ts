/**
 * Node's own File, Blob and FormData, for tests that upload a file.
 *
 * Node's Request reads jsdom's File as empty, so uploads need Node's classes.
 * Vitest restores the globals after each test.
 */

import { Blob as NodeBlob, File as NodeFile } from "node:buffer";
import { vi } from "vitest";

export async function installNodeFiles(): Promise<void> {
	// Node exposes its FormData class only through an instance of it.
	const nodeFormData = (await new Response(new URLSearchParams()).formData())
		.constructor;
	vi.stubGlobal("FormData", nodeFormData);
	vi.stubGlobal("File", NodeFile);
	vi.stubGlobal("Blob", NodeBlob);
}
