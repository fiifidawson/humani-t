/**
 * Node's own File, Blob and FormData, for the tests that upload a file.
 *
 * jsdom brings its own File and FormData, but requests are built by Node's
 * Request, which cannot read jsdom's files: the file part arrives empty. A
 * browser has one implementation of each, so a test that uploads gets one too.
 * Vitest restores the globals after each test (`unstubGlobals`).
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
