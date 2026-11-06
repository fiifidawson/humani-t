# Brand files

This folder is where the official LiGHT and humani-t brand files go. They come from the lab's shared Drive; nothing here may be redrawn or recreated. Files in this folder are served as they are at `/brand/…`, which the CSP allows (`img-src 'self'`).

## Logos

LiGHT's sub-project rule: **the project's logo on the left, the LiGHT logo on the right**, each with its safe area. Until these files exist, [`src/ui/Brand.tsx`](../../src/ui/Brand.tsx) shows the text "humani-t" and "by LiGHT" in Manrope in the same places.

| Drop in this file | What it is | Used for |
| --- | --- | --- |
| `humani-t-logo.svg` | humani-t's logo for light backgrounds | `ProjectMark`: app header (left), footer (left) |
| `humani-t-logo-white.svg` | humani-t's logo in white | `ProjectMark` on the sign-in cover and in the dark theme |
| `light-logo.svg` | The LiGHT wordmark in its original dark and light blue | `LabMark`: app header (right), footer (right) |
| `light-logo-white.svg` | The LiGHT wordmark in white | `LabMark` on the sign-in cover (Dark Blue) and in the dark theme |

LiGHT's wordmark is used only in its original blues, black or white. Light Blue fails contrast on Eggshell (2.35:1), so if the original-colour wordmark is ever too faint on a page, use the black version there rather than altering it.

Then change the two components in `src/ui/Brand.tsx` to render an `<img>` (with `alt="humani-t"` and `alt="LiGHT"`, and the `-white` file when `isOnColour` is set or the theme is dark), keeping the text as the image's accessible name. No other file changes.

## The headline font

Titles use the family name `"Brand Headline"`, declared once in [`src/theme/fonts.css`](../../src/theme/fonts.css). It points at Playfair Display (open licence) until the lab's licensed **Ivy Presto Headline Semibold** is available as self-hostable `.woff2` files under its Adobe licence. To switch:

1. Put the files in `apps/web/src/assets/fonts/` (not in this folder: files there are bundled and given a content hash, so they cache for good), for example `IvyPrestoHeadline-SemiBold.woff2`.
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
