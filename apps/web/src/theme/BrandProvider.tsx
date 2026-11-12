/**
 * Chakra UI with the brand system, writing its styles under the page's CSP nonce.
 *
 * Vite copies the server's nonce into <meta property="csp-nonce">, and the cache
 * below stamps it on every <style> Emotion inserts; the CSP blocks any without it.
 */

import { ChakraProvider } from "@chakra-ui/react";
import createCache, { type EmotionCache } from "@emotion/cache";
import { CacheProvider } from "@emotion/react";
import { type ReactNode, useState } from "react";
import { system } from "./system";

/**
 * The page's style nonce, or `undefined` where there is none (tests). The
 * browser hides the attribute once a CSP applies; the `nonce` property keeps it.
 */
export function readStyleNonce(): string | undefined {
	const meta = document.querySelector<HTMLMetaElement>(
		'meta[property="csp-nonce"]',
	);
	const nonce = meta?.nonce || meta?.getAttribute("nonce");
	return nonce || undefined;
}

function createStyleCache(): EmotionCache {
	const nonce = readStyleNonce();
	return createCache(
		nonce === undefined ? { key: "css" } : { key: "css", nonce },
	);
}

export function BrandProvider({ children }: { children: ReactNode }) {
	const [cache] = useState(createStyleCache);
	return (
		<CacheProvider value={cache}>
			<ChakraProvider value={system}>{children}</ChakraProvider>
		</CacheProvider>
	);
}
