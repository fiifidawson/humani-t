/**
 * Draw public/og-image.png, the 1200×630 link preview image: the tab icon,
 * centred on the light theme's page colour. Run it again once humani-t has a
 * logo (src/assets/brand/README.md).
 *
 *   node scripts/og-image.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "@playwright/test";

// `bg` in src/theme/system.ts, light mode: the page colour the card should match.
const PAGE_BACKGROUND = "#FFFFFF";

const webRoot = join(import.meta.dirname, "..");
const mark = readFileSync(
	join(webRoot, "src", "assets", "brand", "tab.svg"),
	"utf8",
);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.setContent(
	`<body style="margin:0;height:630px;display:grid;place-items:center;background:${PAGE_BACKGROUND}">
		<div style="width:320px;height:320px">${mark.replace("<svg ", '<svg width="100%" height="100%" ')}</div>
	</body>`,
);
await page.screenshot({ path: join(webRoot, "public", "og-image.png") });
await browser.close();
