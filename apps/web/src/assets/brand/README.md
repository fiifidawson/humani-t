# Brand files

The official LiGHT brand files, from the lab's shared Drive and its Brand Guidelines (August 2026). Nothing here may be redrawn or recreated. Files in `src/assets/` are bundled by Vite with a content hash in their name, so Caddy caches them for good (`/assets/*`), which the CSP allows (`img-src 'self'`).

## Logos

LiGHT's sub-project rule: **the project's logo on the left, the LiGHT logo on the right**, each with its safe area. [`src/ui/Brand.tsx`](../../ui/Brand.tsx) fills both slots in the app header, on the sign-in cover and in the footer.

| File | What it is | Used for |
| --- | --- | --- |
| `light-logo.svg` | The LiGHT wordmark in its original dark and light blue (`LIGHT-ORIGINAL.svg` on the Drive) | `LabMark` in the light theme |
| `light-logo-white.svg` | The LiGHT wordmark in white (`LIGHT-LIGHT.svg`) | `LabMark` on the sign-in cover (Dark Blue) and in the dark theme |
| `tab.svg` | LiGHT's light beam, square | The browser tab icon and the link preview image |

The two wordmark files are the Drive's files with two changes, so the app can size them:

- the `viewBox`, `width` and `height` on the `<svg>` element are trimmed from the 1500×1500 canvas to the wordmark's edges;
- the `<mask>` gets `x="0" y="0" width="1500" height="1500"`. Without it the mask's area is measured from the trimmed frame, misses the wordmark, and the logo draws blank.

The artwork inside is untouched. To update one, copy the new file in and make the same two changes; `e2e/layout.spec.ts` fails if the logo draws blank.

The wordmark is used only in its original blues, black or white. Beside humani-t it is secondary, so it is drawn smaller than the project's name (16 px high), with at least its own height clear above and below and an "L" clear to each side.

humani-t has no logo yet; `ProjectMark` shows the name in Manrope until it does. When it exists, add `humani-t-logo.svg` and `humani-t-logo-white.svg` here, render them in `ProjectMark` as `LabMark` does, then point `scripts/og-image.ts` at the logo, run `node scripts/og-image.ts` from `apps/web`, and update `og:image:alt` in `index.html`.

## The headline font

Titles use the family name `"Brand Headline"`, declared once in [`src/theme/fonts.css`](../../theme/fonts.css). It points at Playfair Display (open licence) until the lab's licensed **Ivy Presto Headline Semibold** is available as self-hostable `.woff2` files under its Adobe licence. To switch:

1. Put the files in `apps/web/src/assets/fonts/`, for example `IvyPrestoHeadline-SemiBold.woff2`.
2. In `src/theme/fonts.css`, replace the two `@font-face` blocks with one, keeping the family name:

   ```css
   @font-face {
   	font-family: "Brand Headline";
   	font-style: normal;
   	font-weight: 600;
   	font-display: swap;
   	src: url("../assets/fonts/IvyPrestoHeadline-SemiBold.woff2") format("woff2");
   }
   ```

3. Run `pnpm build` and check a page title in the browser's font panel.

Never link the font from Adobe Fonts (Typekit) or any other host: the CSP allows fonts from this origin only, and a request to Adobe would tell a third party who uses the deployment.
